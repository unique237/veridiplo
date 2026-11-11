"""Append-only, hash-chained audit log (CLAUDE.md section 4.5).

Chain: row_hash = sha256(canonical_json(row fields incl. prev_hash)); prev_hash = row_hash of the previous row
(global order by id). Appends are serialised with a transaction-scoped Postgres advisory lock, so callers should
commit soon after `log_event` (the orchestrator commits after every step). A DB trigger blocks UPDATE/DELETE.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Iterable, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, sha256_obj
from app.core.models import AuditEvent

AUDIT_LOCK_KEY = 7_345_102  # arbitrary constant for pg_advisory_xact_lock


def _ts_iso(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def row_payload(ev: AuditEvent | dict[str, Any]) -> dict[str, Any]:
    """The exact fields covered by row_hash (order irrelevant: canonical JSON sorts keys)."""
    g = (lambda k: ev[k]) if isinstance(ev, dict) else (lambda k: getattr(ev, k))
    return {
        "ts": _ts_iso(g("ts")),
        "case_id": g("case_id"),
        "agent": g("agent"),
        "action": g("action"),
        "input_hash": g("input_hash"),
        "output_hash": g("output_hash"),
        "policy_rule_ids": list(g("policy_rule_ids") or []),
        "kb_chunk_ids": list(g("kb_chunk_ids") or []),
        "policy_mode": g("policy_mode"),
        "actor": g("actor"),
        "detail": g("detail") or {},
        "prev_hash": g("prev_hash"),
    }


def compute_row_hash(ev: AuditEvent | dict[str, Any]) -> str:
    return sha256_obj(row_payload(ev))


def log_event(session: Session, case_id: Optional[str], agent: str, action: str, input_hash: Optional[str] = None,
              output_hash: Optional[str] = None, policy_rule_ids: Iterable[str] = (), kb_chunk_ids: Iterable[str] = (),
              policy_mode: Optional[str] = None, actor: str = "agent", detail: Optional[dict[str, Any]] = None) -> AuditEvent:
    """Append one hash-chained event. Flushes (so `id` is set) but does not commit."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": AUDIT_LOCK_KEY})
    prev = session.execute(select(AuditEvent.row_hash).order_by(AuditEvent.id.desc()).limit(1)).scalar()
    # normalise detail through canonical JSON so what is hashed == what JSONB stores
    clean_detail = json.loads(canonical_json(detail or {}))
    ev = AuditEvent(
        ts=dt.datetime.now(dt.timezone.utc), case_id=case_id, agent=agent, action=action,
        input_hash=input_hash, output_hash=output_hash,
        policy_rule_ids=list(dict.fromkeys(policy_rule_ids)), kb_chunk_ids=list(dict.fromkeys(kb_chunk_ids)),
        policy_mode=policy_mode, actor=actor, detail=clean_detail, prev_hash=prev, row_hash="",
    )
    ev.row_hash = compute_row_hash(ev)
    session.add(ev)
    session.flush()
    return ev


def verify_chain(session: Session, case_id: Optional[str] = None) -> tuple[bool, Optional[int]]:
    """Recompute hashes and check linkage. Returns (ok, first_bad_id).

    With case_id: only that case's rows are recomputed, but each row's prev_hash must equal the stored row_hash of
    its global predecessor, so tampering anywhere before it is also visible via the first affected row.
    """
    q = select(AuditEvent).order_by(AuditEvent.id)
    if case_id is not None:
        q = q.where(AuditEvent.case_id == case_id)
    rows = session.execute(q).scalars().all()
    prev_cache: dict[int, Optional[str]] = {}
    last_hash: Optional[str] = None
    for i, ev in enumerate(rows):
        if compute_row_hash(ev) != ev.row_hash:
            return False, ev.id
        if case_id is None:
            expected_prev = last_hash if i else None
        else:
            expected_prev = session.execute(
                select(AuditEvent.row_hash).where(AuditEvent.id < ev.id).order_by(AuditEvent.id.desc()).limit(1)).scalar()
        prev_cache[ev.id] = expected_prev
        if ev.prev_hash != expected_prev:
            return False, ev.id
        last_hash = ev.row_hash
    return True, None


def get_events(session: Session, case_id: str) -> list[AuditEvent]:
    return list(session.execute(select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)).scalars())


def event_dict(ev: AuditEvent) -> dict[str, Any]:
    d = row_payload(ev)
    d.update(id=ev.id, row_hash=ev.row_hash)
    return d


def head_hash(session: Session, case_id: Optional[str] = None) -> Optional[str]:
    q = select(AuditEvent.row_hash).order_by(AuditEvent.id.desc()).limit(1)
    if case_id:
        q = q.where(AuditEvent.case_id == case_id)
    return session.execute(q).scalar()
