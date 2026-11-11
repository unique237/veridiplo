"""/audit routes: per-case hash-chained trail + chain verification."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import event_dict, get_events, head_hash, verify_chain
from app.core.db import get_db
from app.core.models import AuditEvent

router = APIRouter(tags=["audit"])


@router.get("/audit/verify")
def audit_verify(case_id: Optional[str] = None, db: Session = Depends(get_db)):
    ok, bad = verify_chain(db, case_id)
    n = db.execute(select(func.count()).select_from(AuditEvent)).scalar()
    return {"chain_valid": ok, "first_bad_id": bad, "case_id": case_id, "events_total": n, "head_hash": head_hash(db)}


@router.get("/audit/{case_id}")
def audit_case(case_id: str, db: Session = Depends(get_db)):
    events = get_events(db, case_id)
    ok, bad = verify_chain(db, case_id)
    return {"case_id": case_id, "chain_valid": ok, "first_bad_id": bad, "count": len(events),
            "events": [event_dict(e) for e in events]}
