"""Pydantic contracts exchanged between agents and the API (ontology classes as typed messages)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

VerdictLabel = Literal["CONSISTENT", "INCONSISTENT", "INSUFFICIENT_EVIDENCE", "BLOCKED", "ATTESTATION"]
CheckStatus = Literal["pass", "fail", "unknown"]
PolicyAction = Literal["allow", "redact", "require_consent", "block", "escalate"]
Checkpoint = Literal["before_ingest", "before_search", "before_report", "before_share", "before_export_abroad"]


class DiplomaFields(BaseModel):
    """Typed fields of a diploma (from JSON or from OCR of the diploma image)."""
    case_id: Optional[str] = None
    holder_name: Optional[str] = None
    birth_date: Optional[str] = None
    birth_place: Optional[str] = None
    institution_id: Optional[str] = None
    programme_id: Optional[str] = None
    session_year: Optional[int] = None
    average: Optional[float] = None
    mention: Optional[str] = None
    serial: Optional[str] = None
    signatory_name: Optional[str] = None
    issue_date: Optional[str] = None
    language: Optional[str] = "fr"


class CaseRequest(BaseModel):
    """The intent: who asks, why, with which consent."""
    consent_token: Optional[str] = None
    legal_basis: Optional[str] = None
    purpose: Optional[str] = None  # recruitment | admission | equivalence | ...
    requester_type: Optional[str] = None  # employer | ministry | embassy | university | hr_firm
    requester_country: Optional[str] = "CM"
    verifier_id: Optional[str] = "verifier-default"
    batch_size: int = 1
    lang: Literal["fr", "en"] = "fr"
    fields: Optional[DiplomaFields] = None
    diploma_case_id: Optional[str] = None  # synthetic fixture id (data/synthetic/diplomas/<id>.json)
    scenario_id: Optional[str] = None


class EvidenceItem(BaseModel):
    evidence_id: str  # e.g. "E1"
    kind: str  # pv_row | registry_row | kg_fact | accreditation | signatory
    document_id: Optional[int] = None
    source_file: Optional[str] = None
    sha256: Optional[str] = None
    page: Optional[int] = None
    row_idx: Optional[int] = None
    bbox: Optional[list[float]] = None
    page_size: Optional[list[float]] = None
    chunk_id: Optional[int] = None
    snippet: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    supports: list[str] = Field(default_factory=list)  # diploma fields supported
    contradicts: list[str] = Field(default_factory=list)
    crop_url: Optional[str] = None  # /search/crop/<chunk_id>.png (highlighted)
    ocr_conf: Optional[float] = None


class CheckResult(BaseModel):
    check_id: str  # C1..C8
    field: str
    status: CheckStatus
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str = ""
    detail_en: str = ""
    critical: bool = True
    score: Optional[float] = None


class VerdictOut(BaseModel):
    label: VerdictLabel
    confidence: float = 0.0
    reasons_fr: list[str] = Field(default_factory=list)
    reasons_en: list[str] = Field(default_factory=list)
    needs_human: bool = False


class FiredRule(BaseModel):
    rule_id: str
    checkpoint: str
    action: PolicyAction
    message_fr: str = ""
    message_en: str = ""
    kb_chunk_ids: list[str] = Field(default_factory=list)
    proposal: bool = False


class PolicyDecision(BaseModel):
    checkpoint: str
    action: PolicyAction = "allow"
    fired: list[FiredRule] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)  # gap ids created


class GapOut(BaseModel):
    gap_id: Optional[str] = None
    type: str = "policy"
    title: str
    detail: dict[str, Any] = Field(default_factory=dict)
    checkpoint: Optional[str] = None


class AgentStep(BaseModel):
    agent: str
    node: str  # Y.3172 node: SRC|C|PP|M|P|D|SINK|MLFO
    action: str
    summary: str = ""
    ts: Optional[str] = None
    ms: Optional[int] = None


class CaseResult(BaseModel):
    case_id: str
    policy_mode: Literal["on", "off"]
    status: str
    verdict: Optional[VerdictOut] = None
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    decisions: list[PolicyDecision] = Field(default_factory=list)
    fired_rules: list[FiredRule] = Field(default_factory=list)
    gaps: list[GapOut] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    explanation_fr: str = ""
    explanation_en: str = ""
    shared_output: dict[str, Any] = Field(default_factory=dict)  # what is actually communicated to the verifier
    needs_registrar: bool = False
    holder_notified: bool = False
    archive_request_draft: Optional[str] = None
    report_sha256: Optional[str] = None
    blocked: bool = False
    transfer_flag: bool = False


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    justification: Optional[str] = None
    reviewer: str = "registrar"
