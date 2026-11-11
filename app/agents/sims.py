"""Simulated institutional actors used by the scenario runner (SINK / scenarios).

The human can take over the registrar role in the UI (POST /cases/{id}/review); these sims reproduce the same actions
deterministically for the A/B comparison.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from app.agents.base import Agent, RunContext
from app.core.audit import log_event
from app.core.hashing import sha256_obj
from app.core.schemas import CaseRequest, ReviewIn

DEFAULT_APPROVAL = ("Écart confirmé : la mention et la moyenne du diplôme diffèrent de la ligne du PV de délibération "
                    "citée en preuve ; communication autorisée.")


class VerifierSimAgent(Agent):
    """An employer / university / HR firm submitting verification requests."""
    name = "VerifierSimAgent"
    node = "SINK"
    action = "submit_request"

    @staticmethod
    def make_request(scenario: dict, mode: str, run_tag: Optional[str] = None) -> dict[str, Any]:
        """Case request (JSON dict) from a scenario fixture; unique verifier id per run so the hourly counter of
        PR-BULK-001 is not polluted by repeated demo runs."""
        r = dict(scenario.get("request") or {})
        tag = run_tag or uuid.uuid4().hex[:6]
        req = CaseRequest(
            consent_token=r.get("consent_token"), legal_basis=r.get("legal_basis"), purpose=r.get("purpose"),
            requester_type=r.get("requester_type"), requester_country=r.get("requester_country") or "CM",
            verifier_id=f"{scenario['id']}-{r.get('requester_type', 'verifier')}-{mode}-{tag}",
            batch_size=int(r.get("bulk_count") or (len(r["items"]) if r.get("items") else 1)),
            diploma_case_id=r.get("diploma_case_id"), scenario_id=scenario["id"]).model_dump()
        if r.get("items"):
            req["items"] = r["items"]
        if r.get("channel"):
            req["channel"] = r["channel"]
        req["use_typed_fields"] = True
        return req

    def run(self, ctx: RunContext) -> RunContext:
        return ctx


class RegistrarSimAgent(Agent):
    """Simulated registrar: picks a decision for a pending case (override via scenario `actor_overrides.registrar_sim`)."""
    name = "RegistrarSimAgent"
    node = "SINK"
    action = "review_decision"

    @staticmethod
    def decide(overrides: Optional[dict[str, Any]] = None) -> ReviewIn:
        o = (overrides or {}).get("registrar_sim") or {}
        return ReviewIn(decision=o.get("action", "approve"),
                        justification=o["justification"] if "justification" in o else DEFAULT_APPROVAL,
                        reviewer="registrar-sim")

    def run(self, ctx: RunContext) -> RunContext:
        return ctx


class HolderSimAgent(Agent):
    """Simulated holder portal: receives the notice and the rectification path."""
    name = "HolderSimAgent"
    node = "SINK"
    action = "notify_holder"

    @staticmethod
    def notify(ctx: RunContext, notice_fr: str, notice_en: str, rectification_fr: str = "", rectification_en: str = "") -> dict:
        n = {"channel": "holder_portal", "notice_fr": notice_fr, "notice_en": notice_en,
             "rectification_fr": rectification_fr, "rectification_en": rectification_en}
        ctx.result.holder_notified = True
        log_event(ctx.session, ctx.case_id, "HolderSimAgent", "holder_notified", output_hash=sha256_obj(n),
                  policy_mode=ctx.mode, detail={"channel": "holder_portal", "simulated": True})
        return n

    def run(self, ctx: RunContext) -> RunContext:
        return ctx
