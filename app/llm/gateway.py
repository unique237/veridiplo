"""LLM gateway: providers none | ollama | anthropic | openai; also the FR/EN template renderer.

- `none` (default, offline): deterministic templates only; `rephrase()` returns its input unchanged. No network call.
- cloud providers (anthropic/openai) are opt-in; every call writes a `cross_border_transfer` audit event (Law 2024/017
  Art. 32 as reported) and the prompt is pseudonymised (holder name replaced) before leaving the machine (PR-LLM-TRANSFER-001).
- Whatever the provider returns must keep the citation tokens (e.g. "[E1]") and pass the forbidden-word filter,
  otherwise the deterministic text is kept.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

from app.core.audit import log_event
from app.core.config import get_settings
from app.core.hashing import sha256_obj

log = logging.getLogger("veridiplo.llm")

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Mirror of PR-NOACCUSE-001 (kept in sync by tests/unit/test_policy_engine.py)
FORBIDDEN_WORDS = ["faux", "fake", "fraud", "fraude", "frauduleux", "falsifi", "forged", "forgery", "contrefa",
                   "counterfeit", "usurp", "imposteur", "fraudster"]


# ---------------------------------------------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=4)
def load_templates(lang: str) -> dict[str, Any]:
    with open(PROMPTS_DIR / f"{lang if lang in ('fr', 'en') else 'fr'}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, **vars: Any) -> str:
    """Render `{{ var }}` templates (Jinja2 when installed, identical minimal fallback otherwise)."""
    try:
        from jinja2 import Environment, StrictUndefined  # noqa: F401
        env = Environment(autoescape=False, keep_trailing_newline=False)
        return env.from_string(template).render(**{k: ("" if v is None else v) for k, v in vars.items()}).strip()
    except ImportError:  # pragma: no cover
        return _VAR.sub(lambda m: str(vars.get(m.group(1), "")), template).strip()


def tpl(lang: str, path: str, **vars: Any) -> str:
    node: Any = load_templates(lang)
    for p in path.split("."):
        node = node[p]
    return render(node, **vars)


def has_forbidden(text: str) -> bool:
    from app.policy.engine import contains_any
    return contains_any(text, FORBIDDEN_WORDS)


# ---------------------------------------------------------------------------------------------------------------
# provider calls (isolated so tests can monkeypatch `_complete`)
# ---------------------------------------------------------------------------------------------------------------
def _complete(provider: str, prompt: str, timeout: float = 30.0) -> str:  # pragma: no cover - network
    import httpx
    s = get_settings()
    if provider == "ollama":
        r = httpx.post(f"{s.OLLAMA_URL}/api/generate", json={"model": s.OLLAMA_MODEL, "prompt": prompt, "stream": False}, timeout=timeout)
        r.raise_for_status()
        return r.json().get("response", "")
    if provider == "anthropic":
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                       headers={"x-api-key": s.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
                       json={"model": "claude-3-5-haiku-latest", "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    if provider == "openai":
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=timeout,
                       headers={"Authorization": f"Bearer {s.OPENAI_API_KEY}"},
                       json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise ValueError(f"unknown provider {provider}")


class LLMGateway:
    def __init__(self, provider: Optional[str] = None, session=None, case_id: Optional[str] = None,
                 policy_mode: Optional[str] = None, guard: Optional[Callable[[dict], Any]] = None):
        self.provider = provider or get_settings().LLM_PROVIDER
        self.session = session
        self.case_id = case_id
        self.policy_mode = policy_mode
        self.guard = guard  # callable(llm_ctx) -> PolicyDecision (PolicyGuardian before_export_abroad); None in version A

    @property
    def cloud(self) -> bool:
        return self.provider in ("anthropic", "openai")

    def rephrase(self, text: str, lang: str = "fr", holder_name: Optional[str] = None,
                 must_keep: Iterable[str] = ()) -> str:
        """Optionally rephrase `text`; returns `text` itself when provider=none or the result is unsafe."""
        if self.provider == "none":
            return text
        decision = None
        rule_ids: list[str] = []
        chunks: list[str] = []
        if self.guard is not None:
            decision = self.guard({"provider": self.provider, "sends_personal_data": bool(holder_name)})
            rule_ids = [f.rule_id for f in decision.fired]
            chunks = [c for f in decision.fired for c in f.kb_chunk_ids]
            if decision.action == "block":
                return text
        prompt_text = text.replace(holder_name, "[HOLDER]") if holder_name else text
        prompt = (f"Rephrase this verification explanation in {'French' if lang == 'fr' else 'English'}. Keep every "
                  f"bracketed citation like [E1] unchanged, never use accusatory words, do not add facts:\n{prompt_text}")
        if self.cloud and self.session is not None:
            from app.policy.loader import resolve_ref
            log_event(self.session, self.case_id, "LLMGateway", "cross_border_transfer", input_hash=sha256_obj(prompt),
                      policy_rule_ids=rule_ids or ["PR-LLM-TRANSFER-001"],
                      kb_chunk_ids=chunks or [c for c in [resolve_ref("CM-DP-2024#transfer")] if c],
                      policy_mode=self.policy_mode, detail={"provider": self.provider, "pseudonymised": bool(holder_name),
                                                           "warning": "personal data may leave Cameroon (Law 2024/017 Art. 32 as reported)"})
        try:
            out = _complete(self.provider, prompt).strip()
        except Exception as exc:  # network down -> deterministic text
            log.warning("LLM call failed (%s); keeping template text", exc)
            return text
        if holder_name:
            out = out.replace("[HOLDER]", holder_name)
        if not out or has_forbidden(out) or any(k not in out for k in must_keep):
            if self.session is not None:
                log_event(self.session, self.case_id, "LLMGateway", "llm_output_rejected", policy_mode=self.policy_mode,
                          detail={"reason": "forbidden word or citation lost"})
            return text
        return out
