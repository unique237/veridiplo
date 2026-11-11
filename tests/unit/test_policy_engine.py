"""Policy engine: examples.yaml fixtures, precedence, error handling, forbidden-word list sync (no DB needed)."""
from __future__ import annotations

import yaml
import pytest

from app.llm.gateway import FORBIDDEN_WORDS, has_forbidden
from app.policy import loader
from app.policy.engine import PolicyEngine, contains_any, intersects, merge_context, strongest

EXAMPLES = yaml.safe_load((loader.kb_dir() / "policy_rules" / "examples.yaml").read_text(encoding="utf-8"))["examples"]
ENGINE = PolicyEngine()


@pytest.mark.parametrize("ex", EXAMPLES, ids=[e["name"] for e in EXAMPLES])
def test_examples(ex):
    res = ENGINE.evaluate(ex["checkpoint"], ex.get("context") or {})
    assert not res.errors
    assert set(res.rule_ids) == set(ex["expect"]["fired"])
    assert res.action == ex["expect"]["decision"]


def test_rules_valid():
    assert loader.validate_rules() == []
    assert len(ENGINE.rules) >= 16


def test_precedence():
    assert strongest(["allow", "redact", "block", "escalate"]) == "block"
    assert strongest(["allow", "redact", "escalate"]) == "escalate"
    assert strongest(["allow", "require_consent"]) == "require_consent"
    assert strongest([]) == "allow"


def test_functions():
    assert contains_any("Un FAUX diplôme", ["faux"])
    assert contains_any("Fraudé", ["fraude"])  # accent-insensitive
    assert not contains_any(None, ["faux"])
    assert intersects(["a", "b"], ["b"]) and not intersects(None, ["a"])


def test_expression_error_fails_closed_and_is_reported():
    rules = [{"id": "X-1", "checkpoint": "before_search", "when": "request.nonexistent == 1", "action": "block",
              "message_fr": "", "message_en": ""},
             {"id": "X-2", "checkpoint": "before_search", "when": "this is not python (", "action": "block",
              "message_fr": "", "message_en": ""}]
    res = PolicyEngine(rules).evaluate("before_search", {})
    assert res.fired == [] and len(res.errors) == 2
    assert res.action == "escalate"  # fail closed


def test_unknown_checkpoint_has_no_rules():
    res = ENGINE.evaluate("before_ingest", {})
    assert res.n_rules == 0 and res.action == "allow" and res.fired == []


def test_kb_chunks_resolved():
    res = ENGINE.evaluate("before_search", {"request": {"consent_token": None, "legal_basis": None, "purpose": "recruitment"}})
    f = next(x for x in res.fired if x.rule_id == "PR-CONSENT-001")
    assert f.kb_chunk_ids and all(loader.get_chunk(c) for c in f.kb_chunk_ids)
    chunks = ENGINE.justification("PR-CONSENT-001")
    assert chunks and chunks[0]["text"]


def test_default_context_complete():
    ctx = merge_context({})
    schema = loader.load_context_schema()["namespaces"]
    for ns, v in schema.items():
        assert set(v["keys"]) <= set(ctx[ns]), ns


def test_forbidden_words_match_rule():
    rule = ENGINE.rule("PR-NOACCUSE-001")
    for w in FORBIDDEN_WORDS:
        assert f"'{w}'" in rule["when"], w
    assert has_forbidden("This is FAKE") and not has_forbidden("Incohérence documentaire détectée")
