"""Acceptance tests: S1-S7 x {A (policy off), B (policy on)} against the `expected` sections of data/scenarios/S*.yaml.

Needs the full stack (DB + seeded archive + retrieval/consistency/extraction agents); skipped if the archive is not seeded.
Run: docker compose exec -T api pytest tests/scenarios -q
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.agents.gap_analyst import seed_gaps
from app.agents.scenario_runner import run_scenario
from app.core.db import SessionLocal, init_db
from app.core.models import Chunk

SIDS = [f"S{i}" for i in range(1, 8)]
_cache: dict[str, dict] = {}


@pytest.fixture(scope="module")
def session():
    try:
        init_db()
        s = SessionLocal()
        n = s.execute(select(func.count()).select_from(Chunk)).scalar()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"database unavailable: {exc}")
    if not n:
        pytest.skip("archive not seeded (run `make seed`)")
    seed_gaps(s)
    s.commit()
    yield s
    s.close()


def _run(session, sid):
    if sid not in _cache:
        _cache[sid] = run_scenario(session, sid)
        session.commit()
    return _cache[sid]


@pytest.mark.parametrize("sid", SIDS)
@pytest.mark.parametrize("mode", ["A", "B"])
def test_scenario_expectations(session, sid, mode):
    r = _run(session, sid)
    bad = [f"{a['name']}: {a['detail']}" for a in r["assertions"][mode] if not a["ok"]]
    assert not bad, f"{sid} {mode}: " + "; ".join(bad)


@pytest.mark.parametrize("sid", SIDS)
def test_audit_chain_verifies(session, sid):
    r = _run(session, sid)
    assert r["A"]["audit_chain_valid"] and r["B"]["audit_chain_valid"]
