"""Minimal explicit agent framework (no heavy dependency).

Every runtime agent has a `name`, a Y.3172 `node` (SRC|C|PP|M|P|D|SINK|MLFO), and `run(ctx) -> ctx`.
Messages exchanged between agents are the Pydantic models of app/core/schemas.py (ontology classes as typed data);
the mutable `RunContext` carries them along the plan SRC -> C -> PP -> M -> D -> SINK.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.schemas import (AgentStep, CaseRequest, CaseResult, CheckResult, DiplomaFields, EvidenceItem,
                              FiredRule, GapOut, PolicyDecision, VerdictOut)


@dataclass
class RunContext:
    """State of one pipeline execution (one case, one mode)."""
    session: Session
    case_id: str
    mode: str  # "on" (B, governed) | "off" (A, ungoverned)
    request: CaseRequest
    result: CaseResult
    fields: Optional[DiplomaFields] = None
    diploma_path: Optional[str] = None
    diploma_sha256: Optional[str] = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    verdict: Optional[VerdictOut] = None
    draft: dict[str, Any] = field(default_factory=dict)  # candidate output being governed
    review: dict[str, Any] = field(default_factory=dict)
    halted: bool = False  # set when a checkpoint blocks the pipeline
    scratch: dict[str, Any] = field(default_factory=dict)
    last_summary: str = ""

    @property
    def governed(self) -> bool:
        return self.mode == "on"


class Agent:
    """Base class: subclasses set `name`, `node`, `action` and implement `run`."""
    name: str = "Agent"
    node: str = "MLFO"
    action: str = "run"

    def run(self, ctx: RunContext) -> RunContext:  # pragma: no cover - interface
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.name} node={self.node}>"


__all__ = ["Agent", "RunContext", "AgentStep", "FiredRule", "GapOut", "PolicyDecision"]
