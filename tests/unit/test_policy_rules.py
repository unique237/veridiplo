"""KB / policy-as-code integrity tests (owner: policy-kb agent).

- every `justified_by` of every rule resolves to an EXISTING chunk (id or anchor)  -> fails otherwise
- rule schema, context-schema conformity of `when` expressions
- examples.yaml: reference evaluation (simpleeval) must give the expected fired rules / decision
"""
from __future__ import annotations

import copy
import json
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.policy import loader  # noqa: E402

simpleeval = pytest.importorskip("simpleeval")

REQUIRED_IDS = {
    "PR-CONSENT-001", "PR-PURPOSE-001", "PR-BULK-001", "PR-REDACT-001", "PR-NOACCUSE-001", "PR-REGISTRAR-001",
    "PR-CROSSBORDER-001", "PR-RETENTION-001", "PR-APPROVAL-AUDIT-001", "PR-LLM-TRANSFER-001", "PR-HUMAN-REVIEW-001",
}
PRECEDENCE = ["block", "escalate", "require_consent", "redact", "allow"]


def _fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


def _functions():
    return {
        "len": len,
        "contains_any": lambda text, words: any(_fold(w) in _fold(text) for w in (words or [])),
        "intersects": lambda a, b: bool(set(a or []) & set(b or [])),
    }


def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def evaluate(checkpoint, override):
    """Reference evaluator: returns (fired ids, decision)."""
    schema = loader.load_context_schema()
    ctx = _merge(schema["defaults"], override or {})
    ev = simpleeval.EvalWithCompoundTypes(names=ctx, functions=_functions())
    fired, actions = [], []
    for r in loader.rules_for_checkpoint(checkpoint):
        if ev.eval(r["when"]):
            fired.append(r["id"])
            actions.append(r["action"])
    decision = next((a for a in PRECEDENCE if a in actions), "allow")
    return fired, decision


def test_kb_has_chunks():
    assert len(loader.load_chunks()) >= 150, "KB must contain >= 150 chunks (run python kb/build_kb.py)"


def test_validate_rules_clean():
    errors = loader.validate_rules()
    assert errors == [], "\n".join(errors)


def test_every_justified_by_resolves_to_existing_chunk():
    chunks = loader.load_chunks()
    for r in loader.load_rules():
        for ref in r["justified_by"]:
            cid = loader.resolve_ref(ref)
            assert cid is not None and cid in chunks, f"{r['id']}: justified_by '{ref}' has no real chunk"


def test_required_rule_set_present():
    ids = {r["id"] for r in loader.load_rules()}
    assert REQUIRED_IDS <= ids, f"missing: {REQUIRED_IDS - ids}"


def test_retention_is_marked_proposal():
    r = {x["id"]: x for x in loader.load_rules()}
    assert r["PR-RETENTION-001"].get("proposal") is True
    assert r["PR-BULK-001"].get("proposal") is True


def test_proposals_declare_themselves():
    for r in loader.load_rules():
        if r.get("kind") == "proposal":
            assert r.get("proposal") is True, r["id"]
            assert r.get("justification_note"), r["id"]


def test_fr_en_messages_non_empty():
    for r in loader.load_rules():
        assert r["message_fr"].strip() and r["message_en"].strip(), r["id"]


def test_anchors_point_to_real_chunks():
    chunks = loader.load_chunks()
    for a, cid in loader.load_anchors().items():
        assert cid in chunks, a


def _examples():
    with open(loader.kb_dir() / "policy_rules" / "examples.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["examples"]


@pytest.mark.parametrize("ex", _examples(), ids=lambda e: e["name"])
def test_examples(ex):
    fired, decision = evaluate(ex["checkpoint"], ex.get("context"))
    assert set(fired) == set(ex["expect"]["fired"]), (fired, ex["expect"])
    assert decision == ex["expect"]["decision"]
