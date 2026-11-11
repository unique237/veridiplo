"""GapAnalystAgent (Y.3172 node P): records policy gaps found at runtime and seeds the gap register.

Gap triggers:
  (a) no rule exists for a checkpoint that handles personal data           -> `no_rule`
  (b) fired rules conflict (a hard block next to a plain permission)        -> `rule_conflict`
  (c) a scenario expects a rule that does not exist in the rule set         -> `missing_rule`
  (d) a fired rule is only a proposal / interpretation (carries `gap_id`)   -> gap of that P-id
  (e) verdict-level triggers: missing archive (P2), uncertain/automated result (P1, P6, P11), stored upload (P3)
Runtime gaps are tied to the seeded P-ids (kb/gaps/seed_gaps.yaml) so UI/report can link them to the register.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.base import Agent
from app.core.models import GapRecord
from app.core.schemas import GapOut
from app.policy import loader
from app.policy.engine import EngineResult, PolicyEngine

log = logging.getLogger("veridiplo.gaps")

# checkpoints that process personal data (a rule-less one is a gap)
PERSONAL_DATA_CHECKPOINTS = {"before_ingest", "before_search", "before_report", "before_share", "before_export_abroad"}
# which seeded gap a rule-less checkpoint maps to
CHECKPOINT_GAP = {"before_ingest": "P3"}


@lru_cache(maxsize=1)
def _titles() -> dict[str, dict]:
    try:
        return {g["id"]: g for g in loader.load_gaps()}
    except Exception:  # pragma: no cover
        return {}


class GapAnalystAgent(Agent):
    name = "GapAnalystAgent"
    node = "P"
    action = "analyse_gaps"

    def __init__(self, engine: Optional[PolicyEngine] = None):
        self.engine = engine

    # ---- persistence ---------------------------------------------------------------------------------------
    def record(self, session: Session, case_id: Optional[str], gap_id: Optional[str], kind: str, reason: str,
               checkpoint: Optional[str] = None, scenario_id: Optional[str] = None, extra: Optional[dict[str, Any]] = None,
               seen: Optional[set] = None) -> GapOut:
        """Store (once per case/gap/kind) and return a runtime gap."""
        key = (case_id, gap_id, kind, checkpoint)
        titles = _titles()
        seed = titles.get(gap_id or "")
        if gap_id is None or gap_id not in titles:
            n = session.execute(select(GapRecord.id).order_by(GapRecord.id.desc()).limit(1)).scalar() or 0
            gap_id_out = gap_id or f"RT-{n + 1}"
            title = reason
        else:
            gap_id_out, title = gap_id, seed["title"]
        detail = {"kind": kind, "reason": reason, "case_id": case_id, "proposal": (seed or {}).get("proposal"),
                  "sources": (seed or {}).get("sources"), **(extra or {})}
        out = GapOut(gap_id=gap_id_out, type=(seed or {}).get("type", "policy"), title=title, detail=detail, checkpoint=checkpoint)
        if seen is not None:
            if key in seen:
                return out
            seen.add(key)
        session.add(GapRecord(gap_id=gap_id_out, type=out.type, title=title, detail=detail, case_id=case_id,
                              scenario_id=scenario_id, checkpoint=checkpoint))
        session.flush()
        return out

    def clear_case(self, session: Session, case_id: str) -> None:
        session.execute(delete(GapRecord).where(GapRecord.case_id == case_id))

    # ---- (a) (b) (d): from an engine evaluation ------------------------------------------------------------
    def analyse_decision(self, session: Session, case_id: Optional[str], checkpoint: str, res: EngineResult,
                         engine: PolicyEngine, scenario_id: Optional[str] = None, personal_data: bool = True,
                         seen: Optional[set] = None) -> list[GapOut]:
        gaps: list[GapOut] = []
        # (a) no rule at all for this checkpoint
        if res.n_rules == 0 and personal_data and checkpoint in PERSONAL_DATA_CHECKPOINTS:
            gaps.append(self.record(session, case_id, CHECKPOINT_GAP.get(checkpoint), "no_rule",
                                    f"No policy rule governs checkpoint '{checkpoint}' although personal data is processed "
                                    f"(e.g. stored diploma upload has no retention/ingest rule).",
                                    checkpoint, scenario_id, seen=seen))
        # (b) conflicting rules: a hard block next to a plain permission (an `allow` rule without obligations)
        blocks = [f for f in res.fired if f.action == "block"]
        for f in res.fired:
            r = engine.rule(f.rule_id) or {}
            if f.action == "allow" and blocks and not r.get("params"):
                gaps.append(self.record(session, case_id, None, "rule_conflict",
                                        f"Rules conflict at '{checkpoint}': {f.rule_id} allows while "
                                        f"{', '.join(b.rule_id for b in blocks)} blocks.", checkpoint, scenario_id,
                                        {"rules": [f.rule_id] + [b.rule_id for b in blocks]}, seen=seen))
        # (d) fired rules that are only proposals/interpretations point to the seeded gap they expose
        for f in res.fired:
            gid = (engine.rule(f.rule_id) or {}).get("gap_id")
            if gid:
                r = engine.rule(f.rule_id) or {}
                why = "proposal (not law)" if r.get("proposal") else "our interpretation of the text"
                gaps.append(self.record(session, case_id, gid, "rule_is_" + ("proposal" if r.get("proposal") else "interpretation"),
                                        f"{f.rule_id} fired but is a {why}; the underlying policy gap {gid} remains.",
                                        checkpoint, scenario_id, {"rule_id": f.rule_id}, seen=seen))
        # fail-closed errors
        for e in res.errors:
            gaps.append(self.record(session, case_id, None, "evaluation_error", f"Policy expression error: {e}", checkpoint,
                                    scenario_id, seen=seen))
        return gaps

    # ---- (e): verdict-level ---------------------------------------------------------------------------------
    def analyse_verdict(self, session: Session, case_id: str, label: Optional[str], needs_human: bool, unknown_checks: int,
                        missing_archive: bool, scenario_id: Optional[str] = None, seen: Optional[set] = None) -> list[GapOut]:
        gaps: list[GapOut] = []
        if label == "INSUFFICIENT_EVIDENCE" and missing_archive:
            gaps.append(self.record(session, case_id, "P2", "no_evidence_probative_value",
                                    "No archive evidence found (missing/lost PV): the legal value of digitised copies and of an "
                                    "'insufficient evidence' result is undefined; an archive request was drafted.",
                                    "before_report", scenario_id, seen=seen))
        if needs_human or unknown_checks:
            gaps.append(self.record(session, case_id, "P1", "automated_decision_uncertain",
                                    "Uncertain automated result routed to a human; no operational safeguard in positive law.",
                                    "before_report", scenario_id, seen=seen))
            gaps.append(self.record(session, case_id, "P6", "liability_false_positive",
                                    "Liability for an automated false positive on a name match is undefined.",
                                    "before_report", scenario_id, seen=seen))
            gaps.append(self.record(session, case_id, "P11", "name_variant_reconciliation",
                                    "No reconciliation procedure for name variants / OCR errors between diploma and archive.",
                                    "before_report", scenario_id, seen=seen))
        return gaps

    # ---- (c) scenario expectations -------------------------------------------------------------------------
    def check_scenario(self, session: Session, scenario: dict, engine: PolicyEngine) -> list[GapOut]:
        """A scenario that expects a rule id absent from the rule set creates a `missing_rule` gap."""
        gaps: list[GapOut] = []
        known = engine.rule_ids()
        exp = scenario.get("expected", {})
        sid = scenario.get("id")
        wanted: set[str] = set(scenario.get("rule_ids_used") or [])
        for mode in ("A", "B"):
            m = exp.get(mode) or {}
            wanted |= set(m.get("rules_fired") or [])
            wanted |= set(m.get("rules_not_fired") or [])
        for rid in sorted(wanted - known):
            gaps.append(self.record(session, None, None, "missing_rule",
                                    f"Scenario {sid} expects rule {rid} which does not exist in kb/policy_rules.",
                                    None, sid, {"rule_id": rid}))
        return gaps


def seed_gaps(session: Session) -> int:
    """Insert the seed gap register (kb/gaps/seed_gaps.yaml) into `gaps` (idempotent). Returns rows inserted."""
    n = 0
    existing = set(session.execute(select(GapRecord.gap_id).where(GapRecord.case_id.is_(None))).scalars())
    for g in loader.load_gaps():
        if g["id"] in existing:
            continue
        detail = {k: v for k, v in g.items() if k not in ("id", "type", "title")}
        detail["kind"] = "seed"
        session.add(GapRecord(gap_id=g["id"], type=g.get("type", "policy"), title=g["title"], detail=detail, case_id=None,
                              scenario_id=(g.get("surfaced_by") or [None])[0], checkpoint=None))
        n += 1
    session.flush()
    return n
