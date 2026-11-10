"""Database engine/session helpers. `init_db()` creates the extension and all tables."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(get_settings().DATABASE_URL, pool_pre_ping=True, future=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def SessionLocal() -> Session:
    get_engine()
    return _Session()  # type: ignore[misc]


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db() -> None:
    from app.core import models  # noqa: F401  (register tables)

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    models.Base.metadata.create_all(eng)
    with eng.begin() as conn:
        # full-text indexes (french + simple) and append-only guard on audit_events
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_chunks_fts ON chunks USING GIN (to_tsvector('simple', coalesce(text,'')))"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_chunks_fts_fr ON chunks USING GIN (to_tsvector('french', coalesce(text,'')))"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kbchunks_fts ON kb_chunks USING GIN (to_tsvector('simple', coalesce(text,'')))"))
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql;"""))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_events"))
        conn.execute(text("""CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_events
                             FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"""))


def reset_db() -> None:
    """Drop everything (used by `make reset` / tests)."""
    from app.core import models

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_events")) if _table_exists(conn, "audit_events") else None
    models.Base.metadata.drop_all(eng)
    init_db()


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:n)"), {"n": name}).scalar())
