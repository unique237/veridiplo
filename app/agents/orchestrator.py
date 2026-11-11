"""Orchestrator (Y.3172 MLFO / intent): builds the plan SRC -> C -> PP -> M -> D -> SINK and enforces the Policy Guardian
checkpoints before_ingest, before_search, before_report, before_export_abroad, before_share.

POLICY_MODE=on  (Version B): Guardian + GapAnalyst active, neutral wording, minimal outputs, registrar gate.
POLICY_MODE=off (Version A): no Guardian, no GapAnalyst, naive wording (may say "FAKE"), full records returned.

The CaseResult is persisted progressively in `cases.result` (status 'running' + steps appended after each step) so the UI
can poll GET /cases/{id}; every step and human action is written to the hash-chained audit log.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.agents.base import Agent, RunContext
from app.agents.gap_analyst import GapAnalystAgent
from app.agents.policy_guardian import PolicyGuardianAgent, record_query
from app.agents.reporter import ReporterAgent
from app.agents.sims import HolderSimAgent
from app.core.audit import head_hash, log_event
from app.core.config import get_settings
from app.core.hashing import sha256_file, sha256_obj
from app.core.models import Case
from app.core.schemas import (AgentStep, CaseRequest, CaseResult, CheckResult, DiplomaFields, EvidenceItem, PolicyDecision,
                              ReviewIn, VerdictOut)
from app.llm.gateway import LLMGateway, tpl
from app.policy.engine import PolicyEngine, chunks_by_ids, get_engine

log = logging.getLogger("veridiplo.orchestrator")

MIN_JUSTIFICATION_CHARS = 20  # PR-APPROVAL-AUDIT-001 params.min_justification_chars


def _now_iso() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------------------------------------------
# typed-field loading (robust demo path) with OCR extraction as alternative
# --------------------------------------------------------------------------------------------------------------
def _fixture_path(case_id: str, ext: str = "json") -> Optional[Path]:
    data = Path(get_settings().DATA_DIR)
    for sub in ("synthetic", "sample"):
        p = data / sub / "diplomas" / f"{case_id}.{ext}"
        if p.exists():
            return p
    return None


def load_typed_fields(case_id: str) -> DiplomaFields:
    p = _fixture_path(case_id, "json")
    if p is None:
        raise FileNotFoundError(f"no synthetic diploma fixture '{case_id}'")
    try:
        from app.agents.extraction import fields_from_json  # ingestion agent
        return fields_from_json(str(p))
    except ImportError:
        d = json.loads(p.read_text(encoding="utf-8"))
        return DiplomaFields(**{k: v for k, v in d.items() if k in DiplomaFields.model_fields})


def ocr_fields(path: str, lang: Optional[str] = None) -> DiplomaFields:
    from app.agents.extraction import extract_diploma_fields
    return extract_diploma_fields(path, lang)


# --------------------------------------------------------------------------------------------------------------
class Orchestrator(Agent):
    name = "Orchestrator"
    node = "MLFO"
    action = "run_case"

    def __init__(self, engine: Optional[PolicyEngine] = None, retrieval=None, consistency=None,
                 extract: Optional[Callable[[str], DiplomaFields]] = None):
        self.engine = engine or get_engine()
        self.guardian = PolicyGuardianAgent(self.engine)
        self.gap_analyst = self.guardian.gap_analyst
        self.reporter = ReporterAgent()
        self._retrieval = retrieval
        self._consistency = consistency
        self._extract = extract

    # lazily created so unit tests can run without the retrieval stack
    @property
    def retrieval(self):
        if self._retrieval is None:
            from app.agents.retrieval import EvidenceRetrievalAgent
            self._retrieval = EvidenceRetrievalAgent()
        return self._retrieval

    @property
    def consistency(self):
        if self._consistency is None:
            from app.agents.consistency import ConsistencyAgent
            self._consistency = ConsistencyAgent()
        return self._consistency

    # ----------------------------------------------------------------------------------------------------------
    # persistence / step recording
    # ----------------------------------------------------------------------------------------------------------
    def _persist(self, ctx: RunContext, status: Optional[str] = None, commit: bool = True) -> None:
        case = ctx.session.get(Case, ctx.case_id)
        if status:
            ctx.result.status = status
            case.status = status
        case.result = json.loads(ctx.result.model_dump_json())
        case.policy_mode = ctx.mode
        case.diploma_fields = ctx.fields.model_dump() if ctx.fields else case.diploma_fields
        case.verdict = ctx.result.verdict.label if ctx.result.verdict else None
        if commit:
            ctx.session.commit()

    def _step(self, ctx: RunContext, agent: str, node: str, action: str, fn: Callable[[], Optional[str]],
              rule_ids: tuple = ()) -> None:
        t0 = time.perf_counter()
        summary = ""
        try:
            summary = fn() or ""
        finally:
            ms = int((time.perf_counter() - t0) * 1000)
            ctx.result.steps.append(AgentStep(agent=agent, node=node, action=action, summary=summary, ts=_now_iso(), ms=ms))
            log_event(ctx.session, ctx.case_id, agent, action, output_hash=sha256_obj(summary), policy_rule_ids=rule_ids,
                      policy_mode=ctx.mode, detail={"node": node, "summary": summary, "ms": ms})
            self._persist(ctx)

    def _event(self, ctx: RunContext, agent: str, action: str, **detail: Any) -> None:
        log_event(ctx.session, ctx.case_id, agent, action, policy_mode=ctx.mode, detail=detail)

    # ----------------------------------------------------------------------------------------------------------
    # public API
    # ----------------------------------------------------------------------------------------------------------
    def start(self, session: Session, case_id: str, policy_mode: Optional[str] = None) -> None:
        """Mark a case 'running' immediately (used before launching a background run)."""
        case = session.get(Case, case_id)
        mode = policy_mode or get_settings().POLICY_MODE
        case.status, case.policy_mode = "running", mode
        case.result = {"case_id": case_id, "policy_mode": mode, "status": "running", "steps": []}
        session.commit()

    def run_case(self, session: Session, case_id: str, policy_mode: Optional[str] = None) -> CaseResult:
        case = session.get(Case, case_id)
        if case is None:
            raise KeyError(case_id)
        mode = policy_mode or get_settings().POLICY_MODE
        if mode not in ("on", "off"):
            raise ValueError("policy_mode must be 'on' or 'off'")
        raw = dict(case.request or {})
        request = CaseRequest(**{k: v for k, v in raw.items() if k in CaseRequest.model_fields})
        if case.scenario_id and not request.scenario_id:
            request.scenario_id = case.scenario_id
        result = CaseResult(case_id=case_id, policy_mode=mode, status="running")
        ctx = RunContext(session=session, case_id=case_id, mode=mode, request=request, result=result,
                         diploma_path=case.diploma_path)
        ctx.scratch["raw_request"] = raw
        case.review, case.review_state, case.verdict = None, None, None
        self.gap_analyst.clear_case(session, case_id)
        self.guardian._seen = set()
        self._persist(ctx, "running")
        try:
            self._pipeline(ctx)
        except Exception as exc:  # never leave a case 'running'
            log.exception("pipeline failed for %s", case_id)
            ctx.result.steps.append(AgentStep(agent="Orchestrator", node="MLFO", action="error", summary=f"{type(exc).__name__}: {exc}",
                                              ts=_now_iso()))
            self._event(ctx, "Orchestrator", "pipeline_error", error=str(exc), trace=traceback.format_exc()[-1500:])
            ctx.result.status = "error"
            case.status = "error"
            self._persist(ctx)
            raise
        return ctx.result

    # ----------------------------------------------------------------------------------------------------------
    # plan: SRC -> C -> PP -> M -> D -> SINK
    # ----------------------------------------------------------------------------------------------------------
    def _pipeline(self, ctx: RunContext) -> None:
        raw = ctx.scratch["raw_request"]
        items = raw.get("items")
        bulk = bool(items) or ctx.request.batch_size > 1 and not ctx.request.diploma_case_id and not ctx.request.fields
        plan = (["SRC/C:intake+before_ingest", "PP:extraction"] if not bulk else ["SRC/C:intake"]) + [
            "P:before_search", "M:retrieval", "M:consistency", "D+P:before_report/export/share", "SINK:release"]

        def _plan() -> str:
            return "intent -> plan: " + " | ".join(plan) + f" (policy_mode={ctx.mode}, batch={ctx.request.batch_size})"
        self._step(ctx, "Orchestrator", "MLFO", "plan", _plan)

        if bulk:
            self._bulk(ctx, items or [])
            return

        # SRC -> C : intake (hash, provenance) + before_ingest
        self._step(ctx, "IntakeAgent", "C", "intake", lambda: self._intake(ctx))
        if ctx.governed:
            dec = self._guard(ctx, "before_ingest", rule_node="C")
            if dec.action == "block":
                return self._halt(ctx, dec)

        # PP : field extraction -> ontology instances
        self._step(ctx, "ExtractionAgent", "PP", "extract_fields", lambda: self._extract_fields(ctx))

        # P : before_search
        if ctx.governed:
            dec = self._guard(ctx, "before_search", rule_node="M")
            if dec.action in ("block", "require_consent"):
                return self._halt(ctx, dec)
            record_query(ctx.session, ctx.request.verifier_id, ctx.request.batch_size)
        # M : retrieval + consistency
        self._step(ctx, "EvidenceRetrievalAgent", "M", "retrieve_evidence", lambda: self._retrieve(ctx))
        self._step(ctx, "ConsistencyAgent", "M", "check_consistency", lambda: self._consistency_step(ctx))
        # D / P / SINK
        if ctx.governed:
            self._governed_report(ctx)
        else:
            self._naive_report(ctx)
        ctx.result.report_sha256 = self.reporter.build_pack(json.loads(ctx.result.model_dump_json()))["report_sha256"]
        self._event(ctx, "Orchestrator", "case_finished", status=ctx.result.status, verdict=ctx.verdict.label if ctx.verdict else None,
                    report_sha256=ctx.result.report_sha256)
        self._persist(ctx)

    # ---- SRC/C -----------------------------------------------------------------------------------------------
    def _intake(self, ctx: RunContext) -> str:
        parts = []
        if ctx.diploma_path and Path(ctx.diploma_path).exists():
            ctx.diploma_sha256 = sha256_file(ctx.diploma_path)
            parts.append(f"diploma upload hashed sha256={ctx.diploma_sha256[:16]}... ({Path(ctx.diploma_path).name})")
        elif ctx.request.diploma_case_id:
            img = _fixture_path(ctx.request.diploma_case_id, "png")
            if img:
                ctx.diploma_path = str(img)
                ctx.diploma_sha256 = sha256_file(img)
                parts.append(f"synthetic specimen {ctx.request.diploma_case_id} hashed sha256={ctx.diploma_sha256[:16]}...")
        if not parts:
            parts.append("typed fields only (no file)")
        parts.append(f"provenance: requester={ctx.request.requester_type}/{ctx.request.requester_country}, purpose={ctx.request.purpose}")
        return "; ".join(parts)

    # ---- PP --------------------------------------------------------------------------------------------------
    def _extract_fields(self, ctx: RunContext) -> str:
        raw = ctx.scratch["raw_request"]
        typed = raw.get("use_typed_fields", True)
        if ctx.request.fields:
            ctx.fields, how = ctx.request.fields, "typed fields from request"
        elif ctx.request.diploma_case_id and typed:
            ctx.fields, how = load_typed_fields(ctx.request.diploma_case_id), f"typed JSON fixture {ctx.request.diploma_case_id}"
        elif ctx.diploma_path:
            fn = self._extract or ocr_fields
            ctx.fields, how = fn(ctx.diploma_path), "OCR of uploaded image"
        else:
            raise ValueError("case has no diploma input (fields, diploma_case_id or file)")
        ctx.fields.case_id = ctx.fields.case_id or ctx.case_id
        return f"{how}: holder={ctx.fields.holder_name}, {ctx.fields.institution_id}/{ctx.fields.programme_id}/{ctx.fields.session_year}"

    # ---- M ---------------------------------------------------------------------------------------------------
    def _retrieve(self, ctx: RunContext) -> str:
        ctx.evidence = self.retrieval.retrieve(ctx.session, ctx.fields, naive=not ctx.governed)
        ctx.result.evidence = list(ctx.evidence)
        return f"{len(ctx.evidence)} evidence item(s) ({'naive similarity search' if not ctx.governed else 'hybrid search + KG'})"

    def _consistency_step(self, ctx: RunContext) -> str:
        checks, verdict = self.consistency.check(ctx.session, ctx.fields, ctx.evidence, mode=ctx.mode)
        ctx.checks, ctx.verdict = checks, verdict
        ctx.result.checks, ctx.result.verdict = checks, verdict
        note = ""
        if ctx.governed:
            note = self._discard_lookalikes(ctx)
            # keep only evidence that supports/contradicts a field or is cited by a check (a look-alike is not evidence)
            cited = {i for c in ctx.checks for i in c.evidence_ids}
            ctx.evidence = [e for e in ctx.evidence if e.supports or e.contradicts or e.evidence_id in cited]
            ctx.result.evidence = list(ctx.evidence)
            verdict = ctx.verdict
            checks = ctx.checks
            verdict.needs_human = self._uncertain(ctx)
        return note + f"verdict={verdict.label} confidence={verdict.confidence:.2f} " + ",".join(f"{c.check_id}:{c.status}" for c in checks)

    @staticmethod
    def _scope(e: EvidenceItem) -> str:
        return (e.fields or {}).get("_match", {}).get("scope", "session")

    @staticmethod
    def _uncertain(ctx: RunContext) -> bool:
        """Uncertainty on EXISTING evidence (e.g. name similarity in the 80-92 band): needs a human. An `unknown` caused by
        the absence of any record is not uncertainty - it is missing evidence (archive request path)."""
        return any(c.status == "unknown" and c.evidence_ids for c in ctx.checks)

    def _discard_lookalikes(self, ctx: RunContext) -> str:
        """Version B: a similar name found only OUTSIDE the claimed session/programme is not evidence for this diploma.
        If no in-scope PV/registry row exists the archive is treated as missing (INSUFFICIENT_EVIDENCE + archive request);
        the look-alike is dropped from evidence and from the check texts so it is never used or shown."""
        rows = [e for e in ctx.evidence if e.kind in ("pv_row", "registry_row")]
        out_ids = {e.evidence_id for e in rows if self._scope(e) not in ("session", "kg")}
        if not out_ids or any(e.evidence_id not in out_ids for e in rows):
            return ""
        for c in ctx.checks:
            if set(c.evidence_ids) & out_ids:
                c.evidence_ids = [i for i in c.evidence_ids if i not in out_ids]
                if c.check_id == "C1":
                    c.status = "unknown"
                    c.detail = "Aucune pièce d'archive trouvée pour la session indiquée : preuve insuffisante."
                    c.detail_en = "No archive record found for the stated session: insufficient evidence."
        ctx.evidence = [e for e in ctx.evidence if e.evidence_id not in out_ids]
        v = ctx.verdict
        ctx.verdict = VerdictOut(label="INSUFFICIENT_EVIDENCE", confidence=0.0, needs_human=False,
                                 reasons_fr=["aucune pièce d'archive (PV, registre) n'a été trouvée pour la session indiquée ;"
                                             " un nom proche trouvé ailleurs n'est pas retenu comme preuve."],
                                 reasons_en=["no archive record (PV, registry) was found for the stated session; a similar name "
                                             "found elsewhere is not accepted as evidence."])
        ctx.result.checks, ctx.result.verdict = ctx.checks, ctx.verdict
        self._event(ctx, "Orchestrator", "lookalike_discarded", previous_label=v.label if v else None, discarded=len(out_ids),
                    reason="similar name outside the claimed session is not evidence")
        return "similar name outside the claimed session discarded (missing archive); "

    # ---- P: guardian wrapper ----------------------------------------------------------------------------------
    def _guard(self, ctx: RunContext, checkpoint: str, overrides: Optional[dict] = None, rule_node: str = "P") -> PolicyDecision:
        dec = self.guardian.check(ctx, checkpoint, overrides)
        ctx.result.steps.append(AgentStep(agent="PolicyGuardianAgent", node="P", action=f"checkpoint:{checkpoint}", ts=_now_iso(), ms=0,
                                          summary=f"{dec.action}" + (f" [{', '.join(f.rule_id for f in dec.fired)}]" if dec.fired else " (no rule fired)")
                                          + (f"; gaps {', '.join(dec.gaps)}" if dec.gaps else "")))
        self._persist(ctx)
        return dec

    def _verdict_ns(self, ctx: RunContext) -> dict[str, Any]:
        v = ctx.verdict
        unknown = sum(1 for c in ctx.checks if c.status == "unknown" and c.evidence_ids)  # uncertainty on existing evidence
        conf = v.confidence if v else None
        # an INSUFFICIENT_EVIDENCE verdict without uncertain checks is not a claim about the holder: confidence n/a
        if v and v.label == "INSUFFICIENT_EVIDENCE" and not unknown:
            conf = None
        return {"label": v.label if v else None, "needs_human": bool(unknown), "confidence": conf,
                "unknown_checks": unknown, "evidence_count": self.reporter.evidence_count(ctx)}

    # ---- halted (blocked) ------------------------------------------------------------------------------------
    def _halt(self, ctx: RunContext, dec: PolicyDecision) -> None:
        msgs_fr = self.guardian.messages(dec, "fr") or [dec.fired[0].message_fr if dec.fired else ""]
        msgs_en = self.guardian.messages(dec, "en") or [dec.fired[0].message_en if dec.fired else ""]
        ctx.halted = True
        ctx.result.blocked = True
        ctx.result.verdict = VerdictOut(label="BLOCKED", confidence=0.0, reasons_fr=msgs_fr, reasons_en=msgs_en)
        ctx.result.explanation_fr = tpl("fr", "explain.BLOCKED", messages=" ".join(msgs_fr))
        ctx.result.explanation_en = tpl("en", "explain.BLOCKED", messages=" ".join(msgs_en))
        ctx.result.shared_output = self.reporter.blocked_output(msgs_fr, msgs_en)
        ctx.result.shared_output["rule_ids"] = [f.rule_id for f in dec.fired]
        ctx.result.status = "blocked"
        ctx.result.report_sha256 = self.reporter.build_pack(json.loads(ctx.result.model_dump_json()))["report_sha256"]
        self._event(ctx, "Orchestrator", "request_blocked", checkpoint=dec.checkpoint, rule_ids=[f.rule_id for f in dec.fired],
                    decision=dec.action)
        self._persist(ctx, "blocked")

    # ---- Version A -------------------------------------------------------------------------------------------
    def _naive_report(self, ctx: RunContext) -> None:
        def build() -> str:
            draft = self.reporter.unconstrained_draft(ctx)
            ctx.result.explanation_fr, ctx.result.explanation_en = draft["text_fr"], draft["text_en"]
            ctx.result.shared_output = self.reporter.naive_output(ctx, draft)
            return "naive report (no policy checkpoints; full record returned)"
        self._step(ctx, "ReporterAgent", "D", "build_report_naive", build)
        self._step(ctx, "Orchestrator", "SINK", "release", lambda: "released to verifier without registrar gate")
        self._persist(ctx, "done")

    # ---- Version B -------------------------------------------------------------------------------------------
    def _governed_report(self, ctx: RunContext) -> None:
        rep, v = self.reporter, ctx.verdict
        foreign = (ctx.request.requester_country or "CM") != "CM"
        audience = "third_country_verifier" if foreign else "verifier"
        vns = self._verdict_ns(ctx)
        llm = LLMGateway(session=ctx.session, case_id=ctx.case_id, policy_mode="on",
                         guard=lambda l: self.guardian.check(ctx, "before_export_abroad", {"llm": l}))
        missing_archive = (v.label == "INSUFFICIENT_EVIDENCE" and not vns["needs_human"]
                           and not any(e.kind in ("pv_row", "registry_row") and (e.supports or e.contradicts)
                                       for e in ctx.evidence))
        self.gap_analyst.analyse_verdict(ctx.session, ctx.case_id, v.label, vns["needs_human"],
                                         vns["unknown_checks"], missing_archive, ctx.request.scenario_id, seen=self.guardian._seen)
        # gap rows created by analyse_verdict are mirrored into the result
        self._sync_gaps(ctx)

        out_ns = lambda d, scope="full_record": {"text": d["text"], "fields": d["fields"], "audience": audience, "scope": scope}
        state: dict[str, Any] = {}

        def screen() -> str:
            # 1) screen the unconstrained draft (what a naive generator would write)
            draft = rep.unconstrained_draft(ctx)
            d1 = self.guardian.check(ctx, "before_report", {"verdict": vns, "output": out_ns(draft)})
            wording = self.guardian.first(d1, "PR-NOACCUSE-001")
            if wording:
                self._event(ctx, "ReporterAgent", "wording_rewritten", rule_id="PR-NOACCUSE-001",
                            reason="draft contained forbidden accusatory wording; replaced by the neutral template")
            # 2) final (neutral, optionally LLM-rephrased) text screened again
            final = rep.neutral_draft(ctx, llm)
            d2 = self.guardian.check(ctx, "before_report", {"verdict": vns, "output": out_ns(final)})
            state.update(draft=final, d1=d1, d2=d2, wording_rewritten=bool(wording))
            ctx.result.explanation_fr, ctx.result.explanation_en = final["text_fr"], final["text_en"]
            return (f"draft screened: {d1.action}{' (wording rewritten)' if wording else ''}; final text screened: {d2.action}; "
                    f"fired={[f.rule_id for f in d2.fired] or '-'}")
        self._step(ctx, "ReporterAgent", "D", "draft_and_screen_report", screen)
        final, d2 = state["draft"], state["d2"]

        if d2.action == "block":  # e.g. verdict without evidence, or wording still forbidden
            return self._halt(ctx, d2)
        # verifier output is always minimised (PR-REDACT-001 fires whenever the full record carries personal fields)
        out = rep.minimal_output(ctx, final)

        # needs human (uncertain) -> hold
        hold_reason: Optional[tuple[str, str]] = None
        if d2.action == "escalate":
            f = self.guardian.first(d2, "PR-HUMAN-REVIEW-001")
            if f:
                hold_reason = (f.message_fr, f.message_en)

        # cross-border
        if foreign:
            sc = {"text": final["text"], "fields": [], "audience": audience, "scope": "evidence_pack"}
            d3 = self.guardian.check(ctx, "before_export_abroad", {"output": sc})
            if any(f.rule_id == "PR-CROSSBORDER-001" for f in d3.fired):
                out = rep.attestation(ctx)
                self.guardian.check(ctx, "before_export_abroad", {"output": {**sc, "scope": "attestation"}})
            ctx.result.transfer_flag = any(f.rule_id == "PR-CROSSBORDER-002" for f in d3.fired)
            if ctx.result.transfer_flag:
                self._event(ctx, "PolicyGuardianAgent", "cross_border_transfer", destination=ctx.request.requester_country,
                            purpose=ctx.request.purpose, attestation_hash=out.get("attestation_hash"),
                            basis="Law 2024/017 Art. 32 (as reported)")
            self._sync_gaps(ctx)

        # before_share: registrar gate for INCONSISTENT
        inc = v.label == "INCONSISTENT"
        d4 = self.guardian.check(ctx, "before_share", {"verdict": vns, "review": {"status": "pending" if inc else None}, "output": {
            "audience": audience, "scope": out.get("scope"), "text": final["text"], "fields": []}})
        if d4.action == "escalate" and self.guardian.first(d4, "PR-REGISTRAR-001"):
            f = self.guardian.first(d4, "PR-REGISTRAR-001")
            hold_reason = hold_reason or (f.message_fr, f.message_en)
        self._sync_gaps(ctx)

        def finish() -> str:
            if hold_reason:
                ctx.result.needs_registrar = True
                ctx.review = {"status": "pending"}
                case = ctx.session.get(Case, ctx.case_id)
                case.review_state = "pending"
                case.review = {"status": "pending_review", "reason_fr": hold_reason[0], "reason_en": hold_reason[1], "attempts": []}
                uncertain = v.label != "INCONSISTENT"
                rect_fr = rep.rectification(ctx, "fr")
                rect_en = rep.rectification(ctx, "en")
                note = HolderSimAgent.notify(ctx, rep.holder_notice(ctx, "fr"), rep.holder_notice(ctx, "en"), rect_fr, rect_en)
                held = rep.held_output(ctx, *hold_reason)
                held["rectification_fr"], held["rectification_en"] = rect_fr, rect_en
                held["holder_notice"] = note
                ctx.result.shared_output = held
                if uncertain:
                    ctx.result.explanation_fr += " " + rect_fr
                    ctx.result.explanation_en += " " + rect_en
                self._event(ctx, "Orchestrator", "escalated_to_registrar", rule_ids=[f.rule_id for f in d4.fired + d2.fired
                                                                                       if f.action == "escalate"])
                return "held for registrar review (pending_review); holder notified; nothing communicated"
            ctx.result.shared_output = out
            if missing_archive:
                ctx.result.archive_request_draft = rep.archive_request(ctx)
                ctx.result.shared_output["archive_request_draft"] = ctx.result.archive_request_draft
            return f"released: scope={out.get('scope')}" + (" (minimal attestation + transfer flag)" if out.get("scope") == "attestation" else "")
        self._step(ctx, "Orchestrator", "SINK", "release", finish)
        self._persist(ctx, "pending_review" if hold_reason else "done")

    def _sync_gaps(self, ctx: RunContext) -> None:
        """Mirror runtime gaps stored by the GapAnalyst into the result (dedup)."""
        from sqlalchemy import select
        from app.core.models import GapRecord
        rows = ctx.session.execute(select(GapRecord).where(GapRecord.case_id == ctx.case_id).order_by(GapRecord.id)).scalars().all()
        from app.core.schemas import GapOut
        have = {(g.gap_id, g.checkpoint, g.title) for g in ctx.result.gaps}
        for r in rows:
            if (r.gap_id, r.checkpoint, r.title) not in have:
                ctx.result.gaps.append(GapOut(gap_id=r.gap_id, type=r.type, title=r.title, detail=r.detail, checkpoint=r.checkpoint))
                have.add((r.gap_id, r.checkpoint, r.title))

    # ----------------------------------------------------------------------------------------------------------
    # bulk requests (S3): HR firm, N names
    # ----------------------------------------------------------------------------------------------------------
    def _bulk(self, ctx: RunContext, items: list[dict]) -> None:
        self._step(ctx, "IntakeAgent", "C", "intake", lambda: f"bulk request of {ctx.request.batch_size} holders from "
                   f"{ctx.request.requester_type} (consent={'yes' if ctx.request.consent_token else 'no'})")
        if ctx.governed:
            dec = self._guard(ctx, "before_search", rule_node="M")
            if dec.action in ("block", "require_consent"):
                return self._halt(ctx, dec)
            record_query(ctx.session, ctx.request.verifier_id, ctx.request.batch_size)

        records: list[dict] = []

        def lookup() -> str:
            for it in items:
                f = DiplomaFields(holder_name=it.get("holder_name"), institution_id=it.get("institution_id"),
                                  programme_id=it.get("programme_id"), session_year=it.get("session_year"))
                try:
                    ev = self.retrieval.retrieve(ctx.session, f, naive=not ctx.governed)
                except Exception as exc:  # one bad item never aborts the batch
                    log.warning("bulk item failed: %s", exc)
                    ev = []
                best = max((e for e in ev if e.kind == "pv_row"), key=lambda e: e.score, default=None)
                if ctx.governed:
                    records.append({"holder_ref": sha256_obj(it.get("holder_name"))[:12], "found": best is not None})
                elif best is not None:
                    records.append({"holder_name": it.get("holder_name"), "found": True, "source_file": best.source_file,
                                    "page": best.page, "row": best.row_idx, "matched": best.fields})
                else:
                    records.append({"holder_name": it.get("holder_name"), "found": False})
            return f"{sum(1 for r in records if r['found'])}/{len(items)} holders matched in the archive"
        self._step(ctx, "EvidenceRetrievalAgent", "M", "bulk_retrieve", lookup)
        ctx.result.shared_output = {"scope": "bulk_records", "records_returned": sum(1 for r in records if r["found"]),
                                    "records": records}
        ctx.result.explanation_fr = f"{ctx.result.shared_output['records_returned']} enregistrements retournés."
        ctx.result.explanation_en = f"{ctx.result.shared_output['records_returned']} records returned."
        self._step(ctx, "Orchestrator", "SINK", "release", lambda: "bulk records released")
        ctx.result.status = "done"
        ctx.result.report_sha256 = self.reporter.build_pack(json.loads(ctx.result.model_dump_json()))["report_sha256"]
        self._persist(ctx, "done")

    # ----------------------------------------------------------------------------------------------------------
    # human / simulated registrar review
    # ----------------------------------------------------------------------------------------------------------
    def review(self, session: Session, case_id: str, rv: ReviewIn, actor: str = "human") -> dict[str, Any]:
        """Registrar approve/reject. Version B requires a written justification (PR-APPROVAL-AUDIT-001)."""
        case = session.get(Case, case_id)
        if case is None or not case.result:
            raise KeyError(case_id)
        res = CaseResult(**case.result)
        mode = res.policy_mode
        ctx = RunContext(session=session, case_id=case_id, mode=mode, request=CaseRequest(**{
            k: v for k, v in (case.request or {}).items() if k in CaseRequest.model_fields}), result=res,
            fields=DiplomaFields(**case.diploma_fields) if case.diploma_fields else None, diploma_path=case.diploma_path,
            evidence=list(res.evidence), checks=list(res.checks), verdict=res.verdict)
        agent_name = "RegistrarSimAgent" if actor == "agent" else "Registrar(human)"
        just = (rv.justification or "").strip()
        info = dict(case.review or {})
        attempts = list(info.get("attempts", []))
        attempt = {"ts": _now_iso(), "decision": rv.decision, "reviewer": rv.reviewer, "justification_length": len(just),
                   "simulated": actor == "agent"}
        log_event(session, case_id, agent_name, f"review_{rv.decision}_attempt", input_hash=sha256_obj({"j": just}),
                  policy_mode=mode, actor="human" if actor == "human" else "agent",
                  detail={"reviewer": rv.reviewer, "justification_length": len(just), "simulated": actor == "agent"})

        accepted, alert, dec_ids = True, False, []
        if ctx.governed:
            vns = self._verdict_ns(ctx)
            dec = self.guardian.check(ctx, "before_share", {"verdict": vns, "review": {
                "status": "pending", "action": rv.decision, "justification": just or None, "justification_length": len(just)}})
            dec_ids = [f.rule_id for f in dec.fired]
            if rv.decision == "approve" and dec.action == "block" and self.guardian.first(dec, "PR-APPROVAL-AUDIT-001"):
                accepted = False
                alert = True
                log_event(session, case_id, "PolicyGuardianAgent", "approval_without_justification", policy_mode=mode,
                          policy_rule_ids=["PR-APPROVAL-AUDIT-001"], actor="agent",
                          detail={"reviewer": rv.reviewer, "min_chars": MIN_JUSTIFICATION_CHARS, "result": "approval rejected"})
                log_event(session, case_id, "PolicyGuardianAgent", "supervisor_alert", policy_mode=mode,
                          policy_rule_ids=["PR-APPROVAL-AUDIT-001"],
                          detail={"message_fr": tpl("fr", "supervisor_alert", min_chars=MIN_JUSTIFICATION_CHARS, case_id=case_id),
                                  "message_en": tpl("en", "supervisor_alert", min_chars=MIN_JUSTIFICATION_CHARS, case_id=case_id)})
            elif rv.decision == "reject" and len(just) < MIN_JUSTIFICATION_CHARS:
                accepted = False  # a justification is mandatory for rejections too (our proposal, same threshold)
                log_event(session, case_id, "PolicyGuardianAgent", "rejection_without_justification", policy_mode=mode,
                          detail={"reviewer": rv.reviewer, "min_chars": MIN_JUSTIFICATION_CHARS})
        attempt.update(accepted=accepted, supervisor_alert=alert, rule_ids=dec_ids)
        attempts.append(attempt)

        if not accepted:
            info.update(status="pending_review", approval_accepted=False, approval_rejected=True,
                        supervisor_alert=alert or info.get("supervisor_alert", False), attempts=attempts)
            case.review = info
            case.review_state = "pending"
            self._persist(ctx)
            session.commit()
            return {"accepted": False, "case_status": case.status, "review": info, "rule_ids": dec_ids,
                    "message_fr": "Décision rejetée : justification écrite obligatoire ; alerte envoyée au superviseur." if alert else
                                  "Décision rejetée : justification écrite obligatoire.",
                    "message_en": "Decision rejected: a written justification is mandatory; supervisor alerted." if alert else
                                  "Decision rejected: a written justification is mandatory."}

        # accepted
        state = "approved" if rv.decision == "approve" else "rejected"
        rep = self.reporter
        if rv.decision == "approve":
            if ctx.governed:
                dec2 = self.guardian.check(ctx, "before_share", {"verdict": self._verdict_ns(ctx), "review": {
                    "status": "approved", "action": "approve", "justification": just, "justification_length": len(just)}})
                dec_ids += [f.rule_id for f in dec2.fired]
                foreign = (ctx.request.requester_country or "CM") != "CM"
                final = {"text_fr": res.explanation_fr, "text_en": res.explanation_en, "text": res.explanation_fr + "\n" + res.explanation_en,
                         "fields": []}
                out = rep.minimal_output(ctx, final)
                if foreign:
                    d3 = self.guardian.check(ctx, "before_export_abroad", {"output": {"text": final["text"], "fields": [],
                                              "audience": "third_country_verifier", "scope": "evidence_pack"}})
                    if any(f.rule_id == "PR-CROSSBORDER-001" for f in d3.fired):
                        out = rep.attestation(ctx)
                    res.transfer_flag = any(f.rule_id == "PR-CROSSBORDER-002" for f in d3.fired)
                res.shared_output = out
                if not res.holder_notified:
                    HolderSimAgent.notify(ctx, rep.holder_notice(ctx, "fr"), rep.holder_notice(ctx, "en"),
                                          rep.rectification(ctx, "fr"), rep.rectification(ctx, "en"))
            else:
                res.shared_output = rep.naive_output(ctx, {"text_fr": res.explanation_fr, "text_en": res.explanation_en})
            res.needs_registrar = False
        else:
            res.shared_output = {"scope": "none", "status": "withdrawn",
                                 "explanation_fr": "Le registraire n'a pas confirmé l'incohérence : aucun résultat négatif n'est communiqué.",
                                 "explanation_en": "The registrar did not confirm the inconsistency: no negative result is communicated."}
            res.needs_registrar = False
        info.update(status="approved" if state == "approved" else "rejected", decision=rv.decision, reviewer=rv.reviewer,
                    justification=just, approval_accepted=(rv.decision == "approve"), approval_rejected=False,
                    supervisor_alert=info.get("supervisor_alert", False), attempts=attempts, ts=_now_iso())
        case.review, case.review_state = info, state
        res.status = "reviewed"
        res.report_sha256 = rep.build_pack(json.loads(res.model_dump_json()))["report_sha256"]
        log_event(session, case_id, agent_name, f"review_{rv.decision}", policy_mode=mode, actor="human" if actor == "human" else "agent",
                  policy_rule_ids=dec_ids, output_hash=res.report_sha256,
                  detail={"reviewer": rv.reviewer, "justification": just, "simulated": actor == "agent"})
        self._persist(ctx, "reviewed")
        session.commit()
        return {"accepted": True, "case_status": "reviewed", "review": info, "rule_ids": dec_ids}
