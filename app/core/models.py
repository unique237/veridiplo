"""SQLAlchemy models — the shared storage contract between all agents.

Embedding dimension is 384 (intfloat/multilingual-e5-small; TF-IDF fallback is projected/hashed to 384 too).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 384


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class SourceDocument(Base):
    """An ingested archive document (PV scan, registry, list) with provenance."""
    __tablename__ = "source_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(1024))
    doc_type: Mapped[str] = mapped_column(String(32))  # pv | registry | list | diploma
    issuer: Mapped[Optional[str]] = mapped_column(String(128), index=True)  # institution id
    programme_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    session_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    pages: Mapped[int] = mapped_column(Integer, default=1)
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="ingested")  # queued|processing|ingested|failed
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Chunk(Base):
    """Searchable evidence unit: a PV row, a registry row or a text block, with page + bbox."""
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # pv_row | registry_row | text
    page: Mapped[int] = mapped_column(Integer, default=1)
    row_idx: Mapped[Optional[int]] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # structured fields: matricule, name_raw, name_norm, average, mention, decision, ...
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    bbox: Mapped[Optional[list[float]]] = mapped_column(ARRAY(Float))  # [x0,y0,x1,y1] in page pixel coords
    page_size: Mapped[Optional[list[float]]] = mapped_column(ARRAY(Float))  # [w,h] of the OCR'd page image
    ocr_conf: Mapped[Optional[float]] = mapped_column(Float)
    institution_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    programme_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    session_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    name_norm: Mapped[Optional[str]] = mapped_column(String(256), index=True)
    embedding = mapped_column(Vector(EMBED_DIM), nullable=True)


class KBChunk(Base):
    """Policy knowledge-base chunk (mirror of kb/chunks/*.jsonl, format of CLAUDE.md §4.4)."""
    __tablename__ = "kb_chunks"
    chunk_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    issuer: Mapped[Optional[str]] = mapped_column(String(256))
    date: Mapped[Optional[str]] = mapped_column(String(16))
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    binding: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(16), default="primary")
    article: Mapped[Optional[str]] = mapped_column(Text)
    as_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    lang: Mapped[Optional[str]] = mapped_column(String(8))
    topics: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    pipeline_nodes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    readiness_dims: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    url: Mapped[Optional[str]] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    embedding = mapped_column(Vector(EMBED_DIM), nullable=True)


class PolicyRuleRow(Base):
    __tablename__ = "policy_rules"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(24))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the full YAML rule


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 str
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(24), default="created")  # created|running|done|blocked|pending_review|reviewed
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # consent_token, purpose, requester_*, fields
    diploma_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    diploma_path: Mapped[Optional[str]] = mapped_column(String(1024))
    policy_mode: Mapped[Optional[str]] = mapped_column(String(3))
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)  # CaseResult dump of the latest run
    verdict: Mapped[Optional[str]] = mapped_column(String(24), index=True)
    review_state: Mapped[Optional[str]] = mapped_column(String(16))  # pending|approved|rejected
    review: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    scenario_id: Mapped[Optional[str]] = mapped_column(String(8), index=True)


class AuditEvent(Base):
    """Append-only, hash-chained (prev_hash → row_hash). A DB trigger blocks UPDATE/DELETE."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    case_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    agent: Mapped[str] = mapped_column(String(48))
    action: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    output_hash: Mapped[Optional[str]] = mapped_column(String(64))
    policy_rule_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    kb_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    policy_mode: Mapped[Optional[str]] = mapped_column(String(3))
    actor: Mapped[str] = mapped_column(String(8), default="agent")  # agent | human
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))


class GapRecord(Base):
    __tablename__ = "gaps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_id: Mapped[Optional[str]] = mapped_column(String(16), index=True)  # P1..P6 / S1..S4 / runtime "RT-n"
    type: Mapped[str] = mapped_column(String(16), default="policy")  # policy | standard
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    case_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    scenario_id: Mapped[Optional[str]] = mapped_column(String(8))
    checkpoint: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    """Simple DB-backed queue for the worker (no Celery)."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))  # ingest | ocr | extract
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))


class QueryLog(Base):
    """Verifier query counter used by PR-BULK-001 (queries_last_hour)."""
    __tablename__ = "query_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    verifier_id: Mapped[str] = mapped_column(String(128), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    n_holders: Mapped[int] = mapped_column(Integer, default=1)
