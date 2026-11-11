"""GapAnalyst + Guardian + Reporter logic with fake retrieval/consistency (DB needed for gap rows; skipped otherwise)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.agents.gap_analyst import GapAnalystAgent, seed_gaps
from app.agents.orchestrator import Orchestrator
from app.core.db import SessionLocal, init_db
from app.core.models import Case, GapRecord
from app.core.schemas import CaseRequest, CheckResult, DiplomaFields, EvidenceItem, VerdictOut
from app.policy.engine import PolicyEngine


@pytest.fixture()
def session():
    try:
        init_db()
        s = SessionLocal()
        s.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"database unavailable: {exc}")
    yield s
    s.rollback()
    s.close()


class FakeRetrieval:
    def __init__(self, items): self.items = items
    def retrieve(self, session, fields, naive=False): return list(self.items)


class FakeConsistency:
    def __init__(self, label, checks=(), needs_human=False, conf=0.9):
        self.label, self.checks, self.nh, self.conf = label, list(checks), needs_human, conf
    def check(self, session, fields, evidence, mode="on"):
        return self.checks, VerdictOut(label=self.label, confidence=self.conf, needs_human=self.nh,
                                       reasons_fr=["Le nom ne correspond pas : faux positif possible."], reasons_en=["name mismatch"])


FIELDS = DiplomaFields(holder_name="TEST Holder", institution_id="UFN", programme_id="LDROIT", session_year=2024,
                       average=14.2, mention="Bien", birth_date="2000-01-01", serial="UFN/2024/LDROIT/1")
EV = EvidenceItem(evidence_id="E1", kind="pv_row", source_file="pv/x.pdf", page=1, row_idx=2, snippet="TEST Holder 14.2",
                  fields={"name_raw": "TEST Holder", "average": 14.2, "mention": "Bien", "matricule": "M1"}, supports=["holder_name"], score=1.0)
EV_BAD = EV.model_copy(update={"contradicts": ["mention"], "supports": []})


def make_case(session, **req):
    base = dict(consent_token="CONSENT-DEMO-ABCDEF123456", purpose="recruitment", requester_type="employer", requester_country="CM",
                verifier_id=f"v-{uuid.uuid4().hex[:6]}", fields=FIELDS.model_dump())
    base.update(req)
    c = Case(id=str(uuid.uuid4()), request=CaseRequest(**base).model_dump())
    session.add(c)
    session.commit()
    return c


def orch(label, evidence, checks=(), nh=False, conf=0.9):
    return Orchestrator(engine=PolicyEngine(), retrieval=FakeRetrieval(evidence), consistency=FakeConsistency(label, checks, nh, conf))


def gap_ids(res): return {g.gap_id for g in res.gaps}
def rules(res): return {f.rule_id for f in res.fired_rules}


def test_seed_gaps_idempotent(session):
    seed_gaps(session)
    n = session.execute(select(GapRecord).where(GapRecord.case_id.is_(None))).scalars().all()
    assert {"P1", "P5", "S3"} <= {g.gap_id for g in n}
    assert seed_gaps(session) == 0


def test_s3_style_block_no_consent(session):
    c = make_case(session, consent_token=None, requester_type="hr_firm", batch_size=200)
    res = orch("CONSISTENT", [EV]).run_case(session, c.id, "on")
    assert res.blocked and res.status == "blocked" and res.verdict.label == "BLOCKED"
    assert {"PR-CONSENT-001", "PR-BULK-001"} <= rules(res)
    assert "P5" in gap_ids(res)
    assert "TEST Holder" not in str(res.shared_output)


def test_a_mode_no_guardian_no_gaps_and_fake_wording(session):
    c = make_case(session, consent_token=None)
    chk = [CheckResult(check_id="C4", field="average/mention", status="fail", evidence_ids=["E1"])]
    res = orch("INCONSISTENT", [EV_BAD], chk).run_case(session, c.id, "off")
    assert res.status == "done" and not res.fired_rules and not res.gaps and not res.decisions
    assert "FAKE" in res.explanation_en and res.shared_output["scope"] == "full_record"
    assert res.shared_output["matricule"] == "M1"


def test_b_inconsistent_held_for_registrar_neutral_wording(session):
    c = make_case(session)
    chk = [CheckResult(check_id="C4", field="average/mention", status="fail", evidence_ids=["E1"])]
    res = orch("INCONSISTENT", [EV_BAD], chk).run_case(session, c.id, "on")
    assert res.status == "pending_review" and res.needs_registrar and res.holder_notified
    assert {"PR-NOACCUSE-001", "PR-REGISTRAR-001"} <= rules(res)
    assert {"P1", "P3", "P6"} <= gap_ids(res)
    txt = (res.explanation_fr + res.explanation_en).lower()
    assert "fake" not in txt and "faux" not in txt and "fraud" not in txt
    assert res.shared_output["status"] == "pending_review" and "verdict" not in res.shared_output
    from app.core.audit import verify_chain
    assert verify_chain(session, c.id) == (True, None)


def test_s7_review_without_justification_rejected_then_valid_approval(session):
    from app.core.schemas import ReviewIn
    c = make_case(session)
    chk = [CheckResult(check_id="C4", field="average/mention", status="fail", evidence_ids=["E1"])]
    o = orch("INCONSISTENT", [EV_BAD], chk)
    o.run_case(session, c.id, "on")
    out = o.review(session, c.id, ReviewIn(decision="approve", justification=""), actor="agent")
    assert not out["accepted"] and {"PR-APPROVAL-AUDIT-001", "PR-REGISTRAR-001"} <= set(out["rule_ids"])
    session.refresh(session.get(Case, c.id))
    case = session.get(Case, c.id)
    assert case.review_state == "pending" and case.review["supervisor_alert"]
    from app.core.audit import get_events, verify_chain
    acts = [e.action for e in get_events(session, c.id)]
    assert "approval_without_justification" in acts and "supervisor_alert" in acts
    out = o.review(session, c.id, ReviewIn(decision="approve", justification="Mention differs from PV row 2 page 1, confirmed."), actor="human")
    assert out["accepted"] and "PR-HOLDER-NOTICE-001" in out["rule_ids"]
    case = session.get(Case, c.id)
    assert case.review_state == "approved" and case.status == "reviewed"
    assert case.result["shared_output"]["verdict"] == "INCONSISTENT"
    assert verify_chain(session, c.id) == (True, None)


def test_s7_a_mode_accepts_empty_justification(session):
    from app.core.schemas import ReviewIn
    c = make_case(session)
    o = orch("INCONSISTENT", [EV_BAD], [CheckResult(check_id="C4", field="x", status="fail", evidence_ids=["E1"])])
    o.run_case(session, c.id, "off")
    out = o.review(session, c.id, ReviewIn(decision="approve", justification=""), actor="agent")
    assert out["accepted"] and session.get(Case, c.id).review_state == "approved"


def test_s4_cross_border_attestation(session):
    c = make_case(session, requester_country="AE")
    res = orch("CONSISTENT", [EV], [CheckResult(check_id="C1", field="holder_name", status="pass", evidence_ids=["E1"])]).run_case(session, c.id, "on")
    so = res.shared_output
    assert so["scope"] == "attestation" and so["attestation_hash"] and so["consistent"] == "yes" and res.transfer_flag
    assert {"PR-CROSSBORDER-001", "PR-CROSSBORDER-002"} <= rules(res) and "P4" in gap_ids(res)
    for k in ("average", "mention", "birth_date", "matricule", "evidence_refs"):
        assert k not in so


def test_s4_a_returns_full_transcript(session):
    c = make_case(session, requester_country="AE")
    res = orch("CONSISTENT", [EV]).run_case(session, c.id, "off")
    assert res.shared_output["scope"] == "full_record" and res.shared_output["birth_date"] == "2000-01-01"


def test_s5_insufficient_evidence_archive_request(session):
    c = make_case(session)
    res = orch("INSUFFICIENT_EVIDENCE", [], conf=0.0).run_case(session, c.id, "on")
    assert res.verdict.label == "INSUFFICIENT_EVIDENCE" and res.status == "done"
    assert res.archive_request_draft and "Objet" in res.archive_request_draft and "Subject" in res.archive_request_draft
    assert "P2" in gap_ids(res)


def test_s6_unknown_goes_to_human_with_rectification(session):
    c = make_case(session)
    chk = [CheckResult(check_id="C1", field="holder_name", status="unknown", evidence_ids=["E1"])]
    res = orch("INSUFFICIENT_EVIDENCE", [EV], chk, nh=True, conf=0.55).run_case(session, c.id, "on")
    assert res.status == "pending_review" and res.needs_registrar
    assert "PR-HUMAN-REVIEW-001" in rules(res) and {"P1", "P6"} <= gap_ids(res)
    assert "rectification" in res.explanation_en.lower()


def test_s1_consistent_minimal_output_with_citations(session):
    c = make_case(session)
    res = orch("CONSISTENT", [EV], [CheckResult(check_id="C1", field="holder_name", status="pass", evidence_ids=["E1"])]).run_case(session, c.id, "on")
    assert res.status == "done" and "PR-REDACT-001" in rules(res) and "P3" in gap_ids(res)
    so = res.shared_output
    assert so["scope"] == "evidence_pack" and so["evidence_refs"][0]["page"] == 1
    assert "date_of_birth" not in so and "birth_date" not in so and "average" not in so
    assert res.report_sha256 and len(res.report_sha256) == 64


def test_gap_c_scenario_expects_missing_rule(session):
    g = GapAnalystAgent().check_scenario(session, {"id": "SX", "expected": {"B": {"rules_fired": ["PR-DOES-NOT-EXIST"]}}}, PolicyEngine())
    assert len(g) == 1 and g[0].detail["kind"] == "missing_rule"


def test_gap_b_rule_conflict(session):
    rules_ = [{"id": "R-A", "checkpoint": "before_share", "when": "True", "action": "block", "message_fr": "", "message_en": ""},
              {"id": "R-B", "checkpoint": "before_share", "when": "True", "action": "allow", "message_fr": "", "message_en": ""}]
    eng = PolicyEngine(rules_)
    res = eng.evaluate("before_share", {})
    gaps = GapAnalystAgent(eng).analyse_decision(session, None, "before_share", res, eng)
    assert any(g.detail["kind"] == "rule_conflict" for g in gaps)


def test_report_pack_and_pdf(session):
    from app.agents.reporter import ReporterAgent
    c = make_case(session)
    res = orch("CONSISTENT", [EV], [CheckResult(check_id="C1", field="holder_name", status="pass", evidence_ids=["E1"])]).run_case(session, c.id, "on")
    d = session.get(Case, c.id).result
    rep = ReporterAgent()
    p1, p2 = rep.build_pack(d), rep.build_pack(d)
    assert p1["report_sha256"] == p2["report_sha256"] == res.report_sha256
    pytest.importorskip("reportlab")  # PDF is optional; the JSON pack above is mandatory
    pdf = rep.render_pdf(p1, "fr")
    assert pdf[:4] == b"%PDF"
