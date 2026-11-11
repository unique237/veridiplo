"""Scenario runner: executes data/scenarios/S*.yaml in Version A (policy off) and Version B (policy on), evaluates the
`expected` sections and returns a side-by-side diff. Used by POST /scenarios/{sid}/run, scripts/run_demo.py and the tests.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml
from sqlalchemy.orm import Session

from app.agents.orchestrator import Orchestrator
from app.agents.sims import RegistrarSimAgent, VerifierSimAgent
from app.core.audit import get_events, verify_chain
from app.core.config import get_settings
from app.core.models import Case

NEG_WORDS = ["faux", "fausse", "fake", "fraud", "fraude", "frauduleux", "falsifié", "forged", "contrefait", "counterfeit"]


def scenarios_dir() -> Path:
    for p in (Path(get_settings().DATA_DIR) / "scenarios", Path(__file__).resolve().parents[2] / "data" / "scenarios"):
        if p.exists():
            return p
    raise FileNotFoundError("data/scenarios not found")


def load_scenario(sid: str) -> dict:
    p = scenarios_dir() / f"{sid.upper()}.yaml"
    if not p.exists():
        raise FileNotFoundError(sid)
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def list_scenarios() -> list[dict]:
    out = []
    for p in sorted(scenarios_dir().glob("S*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        out.append({"id": d["id"], "title_fr": d.get("title_fr"), "title_en": d.get("title_en"), "description": d.get("description")})
    return out


# -------------------------------------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def _keys(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _keys(v)
    return out


def run_mode(session: Session, scenario: dict, mode: str, orch: Orchestrator, run_tag: Optional[str] = None) -> dict[str, Any]:
    """Run one scenario in one mode ('off' = A, 'on' = B). Returns a run record (JSON-serialisable)."""
    from app.api.routes_cases import create_case  # local import: avoid import cycle at module load
    req = VerifierSimAgent.make_request(scenario, mode, run_tag)
    case = create_case(session, req, None, scenario_id=scenario["id"], actor="agent")
    res = orch.run_case(session, case.id, mode)
    review_out = None
    overrides = scenario.get("actor_overrides") or {}
    if overrides.get("registrar_sim"):
        rv = RegistrarSimAgent.decide(overrides)
        review_out = orch.review(session, case.id, rv, actor="agent")
        session.refresh(case)
        res = type(res)(**case.result)
    case = session.get(Case, case.id)
    ok, bad = verify_chain(session, case.id)
    events = [e.action for e in get_events(session, case.id)]
    return {"mode": mode, "label": "A" if mode == "off" else "B", "case_id": case.id, "status": case.status,
            "result": json.loads(res.model_dump_json()), "review": case.review, "review_state": case.review_state,
            "review_out": review_out, "audit_chain_valid": ok, "audit_first_bad": bad, "audit_actions": events,
            "audit_n": len(events)}


def _all_text(run: dict) -> str:
    r = run["result"]
    so = r.get("shared_output") or {}
    parts = [r.get("explanation_fr", ""), r.get("explanation_en", ""), json.dumps(so, ensure_ascii=False, default=str)]
    return "\n".join(parts)


def evaluate(scenario: dict, run: dict) -> list[dict[str, Any]]:
    """Assertions from scenario['expected'][A|B] -> list of {name, ok, detail}."""
    exp = (scenario.get("expected") or {}).get(run["label"]) or {}
    r = run["result"]
    so = r.get("shared_output") or {}
    text = _all_text(run)
    ntext = _norm(text)
    checks = r.get("checks") or []
    fails = {c["check_id"] for c in checks if c["status"] == "fail"}
    unknowns = {c["check_id"] for c in checks if c["status"] == "unknown"}
    verdict = (r.get("verdict") or {}).get("label")
    fired = {f["rule_id"] for f in r.get("fired_rules", [])}
    gap_ids = {g.get("gap_id") for g in r.get("gaps", [])}
    out: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any = "") -> None:
        out.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})

    for key, val in exp.items():
        if key == "verdict":
            add("verdict", verdict == val, f"got {verdict}, expected {val}")
        elif key == "verdict_in":
            add("verdict_in", verdict in val, f"got {verdict}, expected one of {val}")
        elif key == "verdict_not":
            add("verdict_not", verdict != val, f"got {verdict}")
        elif key == "failing_checks":
            add("failing_checks", (set(val) <= fails) and (bool(val) or not fails), f"got {sorted(fails)}, expected {val}")
        elif key == "unknown_checks":
            add("unknown_checks", set(val) <= unknowns, f"got {sorted(unknowns)}, expected {val}")
        elif key == "text_contains":
            for w in val:
                add(f"text_contains:{w}", _norm(w) in ntext)
        elif key == "text_not_contains":
            for w in val:
                add(f"text_not_contains:{w}", _norm(w) not in ntext)
        elif key == "rules_fired":
            for rid in val:
                add(f"rule_fired:{rid}", rid in fired, f"fired={sorted(fired)}")
        elif key == "rules_not_fired":
            for rid in val:
                add(f"rule_not_fired:{rid}", rid not in fired)
        elif key == "gaps":
            for g in val:
                add(f"gap:{g}", g in gap_ids, f"gaps={sorted(x for x in gap_ids if x)}")
        elif key == "citations_min":
            n = sum(1 for e in r.get("evidence", []) if e.get("source_file") and e.get("page") is not None)
            add("citations_min", n >= val, f"{n} cited evidence item(s)")
        elif key == "evidence_expected":
            files = " ".join(str(e.get("source_file")) for e in r.get("evidence", []))
            for f in val:
                add(f"evidence_expected:{f}", f in files or Path(f).name in files)
        elif key == "audit_chain_valid":
            add("audit_chain_valid", run["audit_chain_valid"] == val)
        elif key in ("registrar_review_required", "escalated_to_registrar", "human_review_required"):
            add(key, bool(r.get("needs_registrar")) == bool(val), f"needs_registrar={r.get('needs_registrar')}")
        elif key == "holder_notified":
            add(key, bool(r.get("holder_notified")) == bool(val))
        elif key == "communicated_before_registrar_approval":
            leaked = so.get("verdict") is not None or so.get("scope") not in (None, "none")
            add(key, (not leaked) == (not val), f"shared_output scope={so.get('scope')}")
        elif key == "blocked":
            add("blocked", bool(r.get("blocked")) == bool(val))
        elif key == "outcome":
            actual = "BLOCKED" if r.get("blocked") else ("RECORDS_RETURNED" if so.get("scope") == "bulk_records" else verdict)
            add("outcome", actual == val, f"got {actual}")
        elif key == "records_returned_min":
            add(key, int(so.get("records_returned", 0)) >= val, f"{so.get('records_returned', 0)} returned")
        elif key == "records_returned":
            add(key, int(so.get("records_returned", 0)) == val)
        elif key == "no_personal_data_in_response":
            names = [i.get("holder_name") for i in (scenario["request"].get("items") or [])]
            leaked = [n for n in names if n and _norm(n) in ntext]
            add(key, not leaked or not val, f"{len(leaked)} name(s) leaked")
        elif key == "minimal_attestation":
            add(key, (so.get("scope") == "attestation") == bool(val), f"scope={so.get('scope')}")
        elif key == "transfer_flag":
            add(key, bool(r.get("transfer_flag")) == bool(val))
        elif key == "full_transcript_returned":
            add(key, (so.get("scope") == "full_record") == bool(val), f"scope={so.get('scope')}")
        elif key == "output_fields_present":
            add(key, all(k in so and so[k] not in (None, "") for k in val),
                f"missing={[k for k in val if k not in so or so[k] in (None, '')]}")
        elif key == "output_fields_absent":
            present = [k for k in val if k in _keys(so)]
            add(key, not present, f"present={present}")
        elif key == "output_fields_allowed":
            meta = {"scope", "audience", "consistent", "transfer_flag", "explanation_fr", "explanation_en"}
            extra = [k for k in so if k not in set(val) | meta]
            add(key, not extra, f"unexpected={extra}")
        elif key == "attestation_includes_hash":
            add(key, bool(so.get("attestation_hash")) == bool(val))
        elif key == "archive_request_draft":
            add(key, bool(r.get("archive_request_draft")) == bool(val))
        elif key == "uses_similar_name_as_evidence":
            ln = scenario["expected"]["ground_truth"].get("lookalike_name", "")
            ev_txt = _norm(json.dumps(r.get("evidence", []), ensure_ascii=False, default=str))
            add(key, (bool(ln) and _norm(ln) in ev_txt) == bool(val), f"lookalike in evidence={_norm(ln) in ev_txt if ln else None}")
        elif key == "guesses_similar_name":
            ln = scenario["expected"]["ground_truth"].get("lookalike_name", "").split(" ")[0]
            add(key, (bool(ln) and _norm(ln) in ntext) == bool(val))
        elif key == "rectification_path":
            add(key, ("rectification" in ntext) == bool(val))
        elif key == "review_status":
            st = (run.get("review") or {}).get("status")
            add(key, st == val, f"got {st}")
        elif key == "approval_accepted":
            add(key, bool((run.get("review") or {}).get("approval_accepted")) == bool(val))
        elif key == "approval_rejected":
            add(key, bool((run.get("review") or {}).get("approval_rejected")) == bool(val))
        elif key == "supervisor_alert":
            add(key, ("supervisor_alert" in run["audit_actions"]) == bool(val))
        elif key == "audit_event":
            add(key, val in run["audit_actions"])
        elif key in ("false_positive", "missing_archives"):
            continue  # informational (ground-truth annotations)
        else:
            add(f"unchecked:{key}", True, "informational")
    return out


DIFF_KEYS = ["status", "verdict", "blocked", "needs_registrar", "holder_notified", "transfer_flag", "output_scope",
             "rules_fired", "gaps", "evidence_cited", "audit_events"]


def summarise(run: dict) -> dict[str, Any]:
    r = run["result"]
    so = r.get("shared_output") or {}
    return {"status": run["status"], "verdict": (r.get("verdict") or {}).get("label"), "blocked": r.get("blocked"),
            "needs_registrar": r.get("needs_registrar"), "holder_notified": r.get("holder_notified"),
            "transfer_flag": r.get("transfer_flag"), "output_scope": so.get("scope"),
            "rules_fired": sorted({f["rule_id"] for f in r.get("fired_rules", [])}),
            "gaps": sorted({g.get("gap_id") for g in r.get("gaps", []) if g.get("gap_id")}),
            "evidence_cited": sum(1 for e in r.get("evidence", []) if e.get("source_file")),
            "audit_events": run["audit_n"], "audit_chain_valid": run["audit_chain_valid"]}


def side_by_side(a: dict, b: dict) -> list[dict[str, Any]]:
    sa, sb = summarise(a), summarise(b)
    rows = [{"field": k, "A": sa.get(k), "B": sb.get(k), "differs": sa.get(k) != sb.get(k)} for k in DIFF_KEYS]
    rows.append({"field": "text", "A": (a["result"].get("explanation_fr") or "")[:400],
                 "B": (b["result"].get("explanation_fr") or "")[:400], "differs": True})
    return rows


def run_scenario(session: Session, sid: str, orch: Optional[Orchestrator] = None) -> dict[str, Any]:
    """Run scenario `sid` in A and B; returns runs, assertions and the side-by-side diff."""
    orch = orch or Orchestrator()
    sc = load_scenario(sid)
    tag = uuid.uuid4().hex[:6]
    missing = orch.gap_analyst.check_scenario(session, sc, orch.engine)  # gap (c)
    session.commit()
    a = run_mode(session, sc, "off", orch, tag)
    b = run_mode(session, sc, "on", orch, tag)
    ea, eb = evaluate(sc, a), evaluate(sc, b)
    return {"scenario": {"id": sc["id"], "title_fr": sc.get("title_fr"), "title_en": sc.get("title_en"),
                         "description": sc.get("description")},
            "A": a, "B": b, "diff": side_by_side(a, b), "assertions": {"A": ea, "B": eb},
            "passed": all(x["ok"] for x in ea + eb), "scenario_gaps": [g.model_dump() for g in missing]}
