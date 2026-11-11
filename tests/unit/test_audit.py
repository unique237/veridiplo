"""Audit hash chain (needs the Postgres of the compose stack; skipped otherwise)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.audit import log_event, verify_chain
from app.core.db import SessionLocal, init_db


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


def test_chain_links_and_verifies(session):
    cid = f"t-{uuid.uuid4()}"[:36]
    e1 = log_event(session, cid, "A", "one", policy_rule_ids=["PR-X"], kb_chunk_ids=["K#1"], policy_mode="on", detail={"a": 1})
    e2 = log_event(session, cid, "B", "two", actor="human", detail={"nested": {"x": [1, 2]}, "f": 1.5})
    assert e2.prev_hash == e1.row_hash and len(e1.row_hash) == 64
    assert verify_chain(session, cid) == (True, None)
    assert verify_chain(session)[0]


def test_tamper_detected(session):
    cid = f"t-{uuid.uuid4()}"[:36]
    log_event(session, cid, "A", "one")
    e2 = log_event(session, cid, "A", "two", detail={"k": "v"})
    log_event(session, cid, "A", "three")
    # the DB trigger blocks UPDATE; disable it inside this (rolled back) transaction to simulate tampering
    session.execute(text("ALTER TABLE audit_events DISABLE TRIGGER trg_audit_no_update"))
    session.execute(text("UPDATE audit_events SET detail = '{\"k\":\"tampered\"}'::jsonb WHERE id = :i"), {"i": e2.id})
    session.expire_all()
    ok, bad = verify_chain(session, cid)
    assert not ok and bad == e2.id
    ok, bad = verify_chain(session)
    assert not ok and bad == e2.id
    session.rollback()


def test_append_only_trigger(session):
    cid = f"t-{uuid.uuid4()}"[:36]
    e = log_event(session, cid, "A", "one")
    with pytest.raises(Exception):
        session.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": e.id})
    session.rollback()
