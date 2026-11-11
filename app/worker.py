"""Background worker (same image as the API): DB-backed job loop (no Celery).

Jobs live in the ``jobs`` table. ``process_next_job`` claims one row with ``SELECT ... FOR UPDATE SKIP LOCKED``,
dispatches on ``Job.kind`` and records status/error. Supported kinds:

* ``ingest``  payload ``{path, issuer?, doc_type?, session_year?, programme_id?}`` -> ``IntakeAgent.ingest_file``
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from typing import Any, Callable

from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("veridiplo.worker")

POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "2"))


def _job_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    from app.agents.intake import IntakeAgent

    doc = IntakeAgent(workers=1).ingest_file(
        payload["path"], issuer=payload.get("issuer"), doc_type=payload.get("doc_type"),
        session_year=payload.get("session_year"), programme_id=payload.get("programme_id"))
    return {"document_id": doc.id, "status": doc.status, "n_rows": (doc.meta or {}).get("n_rows", 0)}


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {"ingest": _job_ingest}


def process_next_job() -> bool:
    """Claim and run one queued job. Returns True if a job was processed (successfully or not)."""
    from app.core.db import SessionLocal
    from app.core.models import Job

    s = SessionLocal()
    try:
        job = s.execute(select(Job).where(Job.status == "queued").order_by(Job.id).limit(1)
                        .with_for_update(skip_locked=True)).scalar_one_or_none()
        if job is None:
            return False
        job.status = "running"
        s.commit()  # release the row lock; status "running" keeps other workers away
        job_id, kind, payload = job.id, job.kind, dict(job.payload or {})
    finally:
        s.close()

    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        handler = HANDLERS.get(kind)
        if handler is None:
            raise ValueError(f"unknown job kind {kind!r}")
        result = handler(payload)
    except Exception as exc:
        log.exception("job %s (%s) failed", job_id, kind)
        error = f"{type(exc).__name__}: {exc}"[:2000]

    s = SessionLocal()
    try:
        job = s.get(Job, job_id)
        job.status = "failed" if error else "done"
        job.error = error
        if result is not None:
            job.payload = {**payload, "result": result}
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        s.commit()
    finally:
        s.close()
    log.info("job %s (%s) -> %s", job_id, kind, "failed" if error else "done")
    return True


def requeue_stale(max_age_s: int = 3600) -> int:
    """Put jobs stuck in ``running`` (worker crash) back in the queue."""
    from sqlalchemy import update

    from app.core.db import SessionLocal
    from app.core.models import Job

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=max_age_s)
    s = SessionLocal()
    try:
        r = s.execute(update(Job).where(Job.status == "running", Job.created_at < cutoff).values(status="queued"))
        s.commit()
        return r.rowcount or 0
    finally:
        s.close()


def run_worker(poll_seconds: float = POLL_SECONDS, max_iterations: int | None = None) -> None:
    log.info("worker started (poll=%ss)", poll_seconds)
    try:
        from app.core.db import init_db

        init_db()
        n = requeue_stale()
        if n:
            log.info("requeued %d stale jobs", n)
    except Exception:
        log.exception("worker init failed (db not ready?) - will retry in the loop")
    i = 0
    while max_iterations is None or i < max_iterations:
        i += 1
        try:
            if not process_next_job():
                time.sleep(poll_seconds)
        except Exception:
            log.exception("worker loop error")
            time.sleep(poll_seconds)


if __name__ == "__main__":
    run_worker()
