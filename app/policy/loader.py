"""Policy-as-code loader: reads kb/policy_rules/*.yaml, kb/chunks/*.jsonl, kb/chunks/anchors.json.

Standalone (stdlib + pyyaml). The rule *engine* lives in app/policy/engine.py (another module); this module only
loads data and checks referential integrity:

    validate_rules()  -> list[str]   # empty list == OK
    load_rules()      -> list[dict]  # every rule, with `justified_by` resolved in `justified_chunks`
    resolve_ref(ref)  -> chunk_id | None   # chunk id or anchor like "CM-DP-2024#consent"
    get_chunk(ref)    -> dict | None
    search_chunks(q)  -> ranked chunks (pure-python keyword scoring; the DB-backed hybrid search is in app/search)

KB location: env KB_DIR, else <repo>/kb. Swapping KB_DIR (and the ontology) switches domain.
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def kb_dir() -> Path:
    return Path(os.environ.get("KB_DIR") or REPO_ROOT / "kb").resolve()


RULE_REQUIRED = ["id", "checkpoint", "when", "action", "message_fr", "message_en", "justified_by", "pipeline_node", "readiness_dims"]
CHECKPOINTS = {"before_ingest", "before_search", "before_report", "before_share", "before_export_abroad"}
ACTIONS = {"allow", "redact", "require_consent", "block", "escalate"}
NODES = {"SRC", "C", "PP", "M", "P", "D", "SINK"}
KINDS = {"legal", "legal_interpretation", "proposal"}
NON_RULE_FILES = {"examples.yaml", "context_schema.yaml"}


# ----------------------------------------------------------------------------------------------
# chunks / anchors / sources
# ----------------------------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _load_chunks_cached(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in sorted(Path(path).glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    c = json.loads(line)
                    out[c["chunk_id"]] = c
    return out


def load_chunks(kb: Path | None = None) -> dict[str, dict]:
    """All KB chunks keyed by chunk_id (cached; call clear_cache() after rebuilding the KB)."""
    return _load_chunks_cached(str((kb or kb_dir()) / "chunks"))


@lru_cache(maxsize=4)
def _load_anchors_cached(path: str) -> dict[str, str]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_anchors(kb: Path | None = None) -> dict[str, str]:
    """{anchor -> chunk_id} (kb/chunks/anchors.json)."""
    return _load_anchors_cached(str((kb or kb_dir()) / "chunks" / "anchors.json"))


def clear_cache() -> None:
    _load_chunks_cached.cache_clear()
    _load_anchors_cached.cache_clear()


def load_sources(kb: Path | None = None) -> list[dict]:
    with open((kb or kb_dir()) / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def resolve_ref(ref: str, kb: Path | None = None) -> str | None:
    """Resolve a chunk id (CM-DP-2024#c012) or a semantic anchor (CM-DP-2024#consent) to an existing chunk id."""
    chunks = load_chunks(kb)
    if ref in chunks:
        return ref
    return load_anchors(kb).get(ref)


def get_chunk(ref: str, kb: Path | None = None) -> dict | None:
    cid = resolve_ref(ref, kb)
    return load_chunks(kb).get(cid) if cid else None


# ----------------------------------------------------------------------------------------------
# rules
# ----------------------------------------------------------------------------------------------
def _rule_files(kb: Path | None = None) -> list[Path]:
    d = (kb or kb_dir()) / "policy_rules"
    return sorted(p for p in d.glob("*.yaml") if p.name not in NON_RULE_FILES)


def load_context_schema(kb: Path | None = None) -> dict:
    with open((kb or kb_dir()) / "policy_rules" / "context_schema.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_rules(kb: Path | None = None) -> list[dict]:
    """Load every rule from kb/policy_rules/*.yaml (file order, then rule order). Adds `_file` and `justified_chunks`."""
    rules: list[dict] = []
    for p in _rule_files(kb):
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
        if isinstance(data, dict):  # tolerate {rules: [...]}
            data = data.get("rules", [])
        for r in data:
            r = dict(r)
            r["_file"] = p.name
            r["justified_chunks"] = [resolve_ref(x, kb) for x in (r.get("justified_by") or [])]
            rules.append(r)
    return rules


def rules_for_checkpoint(checkpoint: str, kb: Path | None = None) -> list[dict]:
    return [r for r in load_rules(kb) if r.get("checkpoint") == checkpoint]


def _expr_names(expr: str) -> tuple[set[tuple[str, str]], set[str], set[str], str | None]:
    """Static analysis of a `when` expression -> (namespace.key pairs, bare names, called functions, syntax error)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return set(), set(), set(), f"syntax error: {e.msg}"
    pairs, names, calls = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            pairs.add((node.value.id, node.attr))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Lambda, ast.ListComp, ast.GeneratorExp, ast.DictComp, ast.SetComp)):
            return pairs, names, calls, "comprehension/lambda not allowed (simpleeval)"
    return pairs, names, calls, None


def validate_rules(kb: Path | None = None) -> list[str]:
    """Referential-integrity + schema validation. Returns a list of error strings (empty list == valid).

    Checks: required fields; checkpoint/action/pipeline_node/kind enums; unique ids; every `justified_by` reference
    (chunk id or anchor) resolves to an EXISTING chunk; rules with empty justified_by must be proposals and carry a
    `justification_note`; `when` parses and only uses namespaces/keys/functions declared in context_schema.yaml.
    """
    errors: list[str] = []
    try:
        schema = load_context_schema(kb)
    except Exception as e:  # pragma: no cover
        return [f"cannot load context_schema.yaml: {e}"]
    ns = {n: set(v["keys"]) for n, v in schema["namespaces"].items()}
    funcs = set(schema.get("functions", []))
    files = _rule_files(kb)
    if not files:
        return ["no rule files found in policy_rules/"]
    seen: set[str] = set()
    for r in load_rules(kb):
        rid = r.get("id", "<no id>")
        where = f"{rid} ({r['_file']})"
        for f in RULE_REQUIRED:
            if f not in r:
                errors.append(f"{where}: missing required field '{f}'")
        if rid in seen:
            errors.append(f"{where}: duplicate rule id")
        seen.add(rid)
        if r.get("checkpoint") not in CHECKPOINTS:
            errors.append(f"{where}: invalid checkpoint {r.get('checkpoint')!r}")
        if r.get("action") not in ACTIONS:
            errors.append(f"{where}: invalid action {r.get('action')!r}")
        if r.get("pipeline_node") not in NODES:
            errors.append(f"{where}: invalid pipeline_node {r.get('pipeline_node')!r}")
        if r.get("kind") is not None and r["kind"] not in KINDS:
            errors.append(f"{where}: invalid kind {r.get('kind')!r}")
        if not isinstance(r.get("readiness_dims"), list) or not all(isinstance(x, int) and 1 <= x <= 13 for x in r.get("readiness_dims", [])):
            errors.append(f"{where}: readiness_dims must be a list of ints 1..13")
        jb = r.get("justified_by")
        if not isinstance(jb, list):
            errors.append(f"{where}: justified_by must be a list")
            jb = []
        for ref, cid in zip(jb, r.get("justified_chunks", [])):
            if cid is None:
                errors.append(f"{where}: justified_by '{ref}' does not resolve to an existing KB chunk or anchor")
        if not jb:
            if not r.get("proposal"):
                errors.append(f"{where}: empty justified_by is only allowed for proposal: true rules")
            elif not r.get("justification_note"):
                errors.append(f"{where}: proposal with empty justified_by needs a justification_note")
        if r.get("kind") == "proposal" and not r.get("proposal"):
            errors.append(f"{where}: kind: proposal requires proposal: true")
        if r.get("proposal") and r.get("kind") not in (None, "proposal"):
            errors.append(f"{where}: proposal: true requires kind: proposal")
        # expression
        expr = r.get("when")
        if not isinstance(expr, str) or not expr.strip():
            errors.append(f"{where}: 'when' must be a non-empty string")
            continue
        pairs, names, calls, err = _expr_names(expr)
        if err:
            errors.append(f"{where}: when: {err}")
            continue
        for n, k in sorted(pairs):
            if n not in ns:
                errors.append(f"{where}: when uses unknown namespace '{n}'")
            elif k not in ns[n]:
                errors.append(f"{where}: when uses unknown key '{n}.{k}' (not in context_schema.yaml)")
        for fn in sorted(calls - funcs):
            errors.append(f"{where}: when calls unknown function '{fn}()'")
        for n in sorted(names - set(ns) - funcs - {"None", "True", "False"}):
            # bare names that are only attribute bases are namespaces; anything else is unknown
            if not any(n == p[0] for p in pairs):
                errors.append(f"{where}: when uses unknown bare name '{n}'")
    return errors


# ----------------------------------------------------------------------------------------------
# simple local search (no DB) — keyword scoring with accent folding
# ----------------------------------------------------------------------------------------------
def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def search_chunks(q: str, limit: int = 10, source_id: str | None = None, lang: str | None = None, kb: Path | None = None) -> list[dict]:
    """Tiny BM25-like keyword search over the JSONL chunks. Returns chunk dicts plus a `score` key."""
    toks = [t for t in re.findall(r"\w+", _fold(q)) if len(t) > 2]
    if not toks:
        return []
    chunks = [c for c in load_chunks(kb).values() if (not source_id or c["source_id"] == source_id) and (not lang or c["lang"] == lang)]
    docs = [(_fold((c.get("article") or "") + " " + c["text"]), c) for c in chunks]
    n = len(docs)
    df = {t: sum(1 for d, _ in docs if t in d) for t in toks}
    scored = []
    for d, c in docs:
        s = 0.0
        for t in toks:
            tf = d.count(t)
            if tf:
                s += (1 + math.log(tf)) * math.log(1 + n / (1 + df[t]))
        if s > 0:
            scored.append((s / (1 + math.log(1 + len(d) / 800)), c))
    scored.sort(key=lambda x: -x[0])
    return [dict(c, score=round(s, 3)) for s, c in scored[:limit]]


def load_gaps(kb: Path | None = None) -> list[dict]:
    """Gap register: kb/gaps/gap_register.json if built, else the seed YAML."""
    d = (kb or kb_dir()) / "gaps"
    j = d / "gap_register.json"
    if j.exists():
        return json.loads(j.read_text(encoding="utf-8"))["gaps"]
    with open(d / "seed_gaps.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["gaps"]


if __name__ == "__main__":  # python -m app.policy.loader  -> quick integrity report
    errs = validate_rules()
    rules = load_rules()
    print(f"{len(rules)} rules, {len(load_chunks())} chunks, {len(load_anchors())} anchors")
    for e in errs:
        print("ERROR:", e)
    raise SystemExit(1 if errs else 0)
