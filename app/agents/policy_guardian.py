"""PolicyGuardianAgent (Y.3172 node P): evaluates the policy-as-code rules at every checkpoint.

Checkpoints: before_ingest, before_search, before_report, before_share, before_export_abroad.
Returns a PolicyDecision (strongest action by precedence block > escalate > require_consent > redact > allow), the fired
rules with the resolved KB chunk ids that justify them, writes one audit event per checkpoint, and calls the GapAnalyst.
Not instantiated in Version A (POLICY_MODE=off).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import Agent, RunContext
from app.agents.gap_analyst import GapAnalystAgent
from app.core.audit import log_event
from app.core.hashing import sha256_obj
from app.core.models import QueryLog
from app.core.schemas import FiredRule, GapOut, PolicyDecision
from app.policy.engine import PolicyEngine, get_engine, merge_context

CHECKPOINTS = ("before_ingest", "before_search", "before_report", "before_share", "before_export_abroad")

_TOKEN_RE = re.compile(r"^CONSENT-[A-Z]+-[0-9A-F]{8,}$")


def validate_consent(token: Optional[str], purpose: Optional[str] = None) -> Optional[bool]:
    """Simulated engine-side consent-token validation (signature/holder/expiry are out of scope of the sandbox).

    None when no token; False when malformed or flagged expired/revoked; True otherwise. Gap S5 (standards): there is no
    standard for digital consent evidence, hence this stub.
    """
    if token is None:
        return None
    t = token.strip().upper()
    if not _TOKEN_RE.match(t) and not t.startswith("TOK-"):
        return False
    return not any(w in t for w in ("EXPIRED", "REVOKED", "INVALID"))


def queries_last_hour(session: Session, verifier_id: Optional[str]) -> int:
    if not verifier_id:
        return 0
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    return int(session.execute(select(func.coalesce(func.sum(QueryLog.n_holders), 0)).where(
        QueryLog.verifier_id == verifier_id, QueryLog.ts >= since)).scalar() or 0)


def record_query(session: Session, verifier_id: Optional[str], n_holders: int = 1) -> None:
    session.add(QueryLog(verifier_id=verifier_id or "verifier-default", n_holders=max(1, n_holders)))
    session.flush()


class PolicyGuardianAgent(Agent):
    name = "PolicyGuardianAgent"
    node = "P"
    action = "checkpoint"

    def __init__(self, engine: Optional[PolicyEngine] = None, gap_analyst: Optional[GapAnalystAgent] = None):
        self.engine = engine or get_engine()
        self.gap_analyst = gap_analyst or GapAnalystAgent(self.engine)
        self._seen: set = set()

    # ---- context ------------------------------------------------------------------------------------------
    def base_context(self, ctx: RunContext) -> dict[str, dict[str, Any]]:
        rq = ctx.request
        valid = validate_consent(rq.consent_token, rq.purpose)
        return merge_context({
            "request": {"consent_token": rq.consent_token, "consent_valid": valid, "legal_basis": rq.legal_basis,
                        "purpose": rq.purpose, "requester_type": rq.requester_type,
                        "requester_country": rq.requester_country, "batch_size": rq.batch_size, "scope": None},
            "verifier": {"id": rq.verifier_id, "queries_last_hour": queries_last_hour(ctx.session, rq.verifier_id)},
            "upload": {"age_days": 0 if ctx.diploma_path else None},
        })

    # ---- main entry ---------------------------------------------------------------------------------------
    def check(self, ctx: RunContext, checkpoint: str, overrides: Optional[dict[str, dict[str, Any]]] = None,
              personal_data: bool = True) -> PolicyDecision:
        assert checkpoint in CHECKPOINTS, checkpoint
        context = self.base_context(ctx)
        for ns, vals in (overrides or {}).items():
            context.setdefault(ns, {}).update(vals)
        res = self.engine.evaluate(checkpoint, context)
        scenario_id = ctx.request.scenario_id
        gaps = self.gap_analyst.analyse_decision(ctx.session, ctx.case_id, checkpoint, res, self.engine, scenario_id,
                                                 personal_data, seen=self._seen)
        for g in gaps:
            if not any(x.gap_id == g.gap_id and x.checkpoint == g.checkpoint and x.title == g.title for x in ctx.result.gaps):
                ctx.result.gaps.append(g)
        decision = PolicyDecision(checkpoint=checkpoint, action=res.action, fired=res.fired,
                                  gaps=list(dict.fromkeys(g.gap_id for g in gaps if g.gap_id)))
        ctx.result.decisions.append(decision)
        known = {f.rule_id for f in ctx.result.fired_rules}
        ctx.result.fired_rules += [f for f in res.fired if f.rule_id not in known]
        log_event(ctx.session, ctx.case_id, self.name, f"checkpoint:{checkpoint}",
                  input_hash=sha256_obj({k: v for k, v in context.items() if k != "output"}),
                  output_hash=sha256_obj({"action": res.action, "fired": res.rule_ids}),
                  policy_rule_ids=res.rule_ids, kb_chunk_ids=res.kb_chunk_ids, policy_mode="on",
                  detail={"decision": res.action, "n_rules": res.n_rules, "errors": res.errors,
                          "gap_ids": decision.gaps, "messages_en": [f.message_en for f in res.fired]})
        return decision

    def run(self, ctx: RunContext) -> RunContext:  # not used as a pipeline stage; the Orchestrator calls check()
        return ctx

    # ---- helpers ------------------------------------------------------------------------------------------
    @staticmethod
    def first(decision: PolicyDecision, rule_id: str) -> Optional[FiredRule]:
        return next((f for f in decision.fired if f.rule_id == rule_id), None)

    @staticmethod
    def messages(decision: PolicyDecision, lang: str = "fr") -> list[str]:
        return [(f.message_fr if lang == "fr" else f.message_en) for f in decision.fired if f.action != "allow"]
