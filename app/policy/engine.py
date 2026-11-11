"""Policy-as-code engine: evaluates kb/policy_rules/*.yaml `when` expressions with simpleeval (no eval).

Context contract: kb/policy_rules/context_schema.yaml (all namespaces/keys always present).
Precedence of the final action: block > escalate > require_consent > redact > allow (strongest fired wins).
Expression errors fail CLOSED (treated as `escalate`, recorded in `EngineResult.errors`) as required by the
context schema; callers (PolicyGuardian) raise a runtime gap and write an audit event.
"""
from __future__ import annotations

import copy
import logging
import unicodedata
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from simpleeval import EvalWithCompoundTypes, InvalidExpression
from sqlalchemy.orm import Session

from app.core.schemas import FiredRule, PolicyAction
from app.policy import loader

log = logging.getLogger("veridiplo.policy")

PRECEDENCE: list[str] = ["block", "escalate", "require_consent", "redact", "allow"]
_RANK = {a: i for i, a in enumerate(PRECEDENCE)}

DEFAULT_CONTEXT: dict[str, dict[str, Any]] = {
    "request": {"consent_token": None, "consent_valid": None, "legal_basis": None, "purpose": None,
                "requester_type": None, "requester_country": "CM", "batch_size": 1, "scope": None},
    "verifier": {"id": None, "queries_last_hour": 0},
    "upload": {"age_days": None},
    "verdict": {"label": None, "needs_human": False, "confidence": None, "unknown_checks": 0, "evidence_count": 0},
    "review": {"status": None, "action": None, "justification": None, "justification_length": 0},
    "output": {"text": "", "fields": [], "audience": None, "scope": None},
    "llm": {"provider": "none", "sends_personal_data": False},
}


def _fold(s: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(s).lower()) if not unicodedata.combining(c))


def contains_any(text: Optional[str], words: Optional[Iterable[str]]) -> bool:
    """Case- and accent-insensitive substring test (None text -> False)."""
    if not text:
        return False
    t = _fold(text)
    return any(_fold(w) in t for w in (words or []))


def intersects(a: Optional[Iterable[Any]], b: Optional[Iterable[Any]]) -> bool:
    return bool(set(a or []) & set(b or []))


def merge_context(overrides: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, dict[str, Any]]:
    """Defaults deep-merged with `overrides` (namespace -> {key: value}); unknown keys are kept."""
    ctx = copy.deepcopy(DEFAULT_CONTEXT)
    for ns, vals in (overrides or {}).items():
        ctx.setdefault(ns, {}).update(vals or {})
    return ctx


@dataclass
class RuleEval:
    rule: dict
    fired: bool
    error: Optional[str] = None


@dataclass
class EngineResult:
    checkpoint: str
    action: PolicyAction = "allow"
    fired: list[FiredRule] = field(default_factory=list)
    evaluated: list[RuleEval] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    n_rules: int = 0  # number of rules defined for this checkpoint

    @property
    def rule_ids(self) -> list[str]:
        return [f.rule_id for f in self.fired]

    @property
    def kb_chunk_ids(self) -> list[str]:
        out: list[str] = []
        for f in self.fired:
            out += [c for c in f.kb_chunk_ids if c not in out]
        return out


def strongest(actions: Iterable[str]) -> PolicyAction:
    best = "allow"
    for a in actions:
        if _RANK.get(a, 99) < _RANK[best]:
            best = a
    return best  # type: ignore[return-value]


class PolicyEngine:
    def __init__(self, rules: Optional[list[dict]] = None, kb=None):
        self.kb = kb
        self.rules = rules if rules is not None else loader.load_rules(kb)

    # ---- helpers -------------------------------------------------------------------------------------------
    def rule(self, rule_id: str) -> Optional[dict]:
        return next((r for r in self.rules if r["id"] == rule_id), None)

    def rule_ids(self) -> set[str]:
        return {r["id"] for r in self.rules}

    def rules_for(self, checkpoint: str) -> list[dict]:
        return [r for r in self.rules if r.get("checkpoint") == checkpoint]

    def _evaluator(self, ctx: dict[str, dict[str, Any]]) -> EvalWithCompoundTypes:
        names = {ns: SimpleNamespace(**vals) for ns, vals in ctx.items()}
        ev = EvalWithCompoundTypes(names=names)
        ev.functions.update({"len": len, "contains_any": contains_any, "intersects": intersects})
        return ev

    # ---- evaluation ----------------------------------------------------------------------------------------
    def evaluate(self, checkpoint: str, context: Optional[dict[str, dict[str, Any]]] = None) -> EngineResult:
        ctx = merge_context(context)
        res = EngineResult(checkpoint=checkpoint)
        ev = self._evaluator(ctx)
        for r in self.rules_for(checkpoint):
            res.n_rules += 1
            try:
                fired = bool(ev.eval(r["when"]))
                res.evaluated.append(RuleEval(r, fired))
            except (InvalidExpression, AttributeError, TypeError, ValueError, SyntaxError, KeyError) as exc:
                msg = f"{r['id']}: {type(exc).__name__}: {exc}"
                log.error("policy expression error %s", msg)
                res.errors.append(msg)
                res.evaluated.append(RuleEval(r, False, msg))
                continue
            if fired:
                res.fired.append(self.to_fired(r))
        actions = [f.action for f in res.fired]
        if res.errors:
            actions.append("escalate")  # fail closed
        res.action = strongest(actions)
        return res

    @staticmethod
    def to_fired(r: dict) -> FiredRule:
        return FiredRule(
            rule_id=r["id"], checkpoint=r["checkpoint"], action=r["action"],
            message_fr=r.get("message_fr", ""), message_en=r.get("message_en", ""),
            kb_chunk_ids=[c for c in (r.get("justified_chunks") or []) if c], proposal=bool(r.get("proposal")),
        )

    # ---- justification chunks ------------------------------------------------------------------------------
    def justification(self, rule_id: str, session: Optional[Session] = None, max_chars: int = 700) -> list[dict]:
        """KB chunks that justify a rule (from the DB mirror when a session is given, else from the JSONL files)."""
        r = self.rule(rule_id)
        if not r:
            return []
        return chunks_by_ids([c for c in r.get("justified_chunks") or [] if c], session, max_chars)


def chunks_by_ids(chunk_ids: list[str], session: Optional[Session] = None, max_chars: int = 700) -> list[dict]:
    out: list[dict] = []
    found: dict[str, dict] = {}
    if session is not None and chunk_ids:
        try:
            from sqlalchemy import select
            from app.core.models import KBChunk
            for c in session.execute(select(KBChunk).where(KBChunk.chunk_id.in_(chunk_ids))).scalars():
                found[c.chunk_id] = {"chunk_id": c.chunk_id, "source_id": c.source_id, "title": c.title, "article": c.article,
                                     "binding": c.binding, "kind": c.kind, "as_reported": c.as_reported, "url": c.url,
                                     "text": c.text}
        except Exception:  # DB mirror not loaded -> fall back to files
            log.warning("kb_chunks lookup failed, using JSONL files", exc_info=True)
    for cid in chunk_ids:
        c = found.get(cid) or loader.get_chunk(cid)
        if not c:
            continue
        d = {k: c.get(k) for k in ("chunk_id", "source_id", "title", "article", "binding", "kind", "as_reported", "url")}
        d["text"] = (c.get("text") or "")[:max_chars]
        out.append(d)
    return out


_default_engine: Optional[PolicyEngine] = None


def get_engine(reload: bool = False) -> PolicyEngine:
    global _default_engine
    if _default_engine is None or reload:
        loader.clear_cache()
        _default_engine = PolicyEngine()
    return _default_engine
