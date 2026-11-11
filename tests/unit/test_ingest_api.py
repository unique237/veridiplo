"""POST /ingest (sync + queued), GET /ingest/status and the worker job loop (needs Postgres)."""
from __future__ import annotations

import io
import uuid

import pytest

from tests.unit.test_ingestion import _db_ok

pytestmark = pytest.mark.skipif(not _db_ok(), reason="database not reachable")


def _csv(tag: str) -> bytes:
    return (f"matricule,nom,prenoms,moyenne,mention,session\n{tag}01,TESTAPI,Alpha Beta,14,5,Bien,2098\n"
            .replace("14,5", "14.5")).encode()


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes_ingest import router
    from app.core.db import init_db

    init_db()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _cleanup(sha: str) -> None:
    from app.core.db import SessionLocal
    from app.core.models import SourceDocument

    s = SessionLocal()
    s.query(SourceDocument).filter(SourceDocument.sha256 == sha).delete()
    s.commit()
    s.close()


def test_ingest_sync_duplicate_and_status(client):
    from app.core.hashing import sha256_bytes

    data = _csv(uuid.uuid4().hex[:6].upper())
    files = {"file": ("UFN_2098.csv", io.BytesIO(data), "text/csv")}
    try:
        r = client.post("/ingest?sync=true", files=files, data={"doc_type": "registry"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ingested" and body["n_rows"] == 1 and body["issuer"] == "UFN"
        r2 = client.post("/ingest?sync=true", files={"file": ("again.csv", io.BytesIO(data), "text/csv")})
        assert r2.json()["duplicate"] is True and r2.json()["document_id"] == body["document_id"]
        st = client.get("/ingest/status", params={"document_id": body["document_id"]}).json()
        assert st["status"] == "ingested"
        assert client.get("/ingest/status").json()["documents"]["total"] >= 1
    finally:
        _cleanup(sha256_bytes(data))


def test_ingest_rejects_bad_type_and_empty(client):
    assert client.post("/ingest", files={"file": ("x.exe", io.BytesIO(b"abc"), "application/octet-stream")}).status_code == 415
    assert client.post("/ingest", files={"file": ("x.csv", io.BytesIO(b""), "text/csv")}).status_code == 400
    assert client.post("/ingest", files={"file": ("x.csv", io.BytesIO(b"a"), "text/csv")},
                       data={"doc_type": "nonsense"}).status_code == 422


def test_ingest_queued_then_worker(client):
    from app.core.hashing import sha256_bytes
    from app.worker import process_next_job

    data = _csv(uuid.uuid4().hex[:6].upper())
    try:
        r = client.post("/ingest", files={"file": ("UFN_2098.csv", io.BytesIO(data), "text/csv")})
        assert r.status_code == 200 and r.json()["status"] == "queued"
        jid = r.json()["job_id"]
        assert client.get("/ingest/status", params={"job_id": jid}).json()["status"] == "queued"
        # drain the queue (other queued jobs from earlier runs may precede ours)
        for _ in range(50):
            if client.get("/ingest/status", params={"job_id": jid}).json()["status"] != "queued":
                break
            process_next_job()
        st = client.get("/ingest/status", params={"job_id": jid}).json()
        assert st["status"] == "done", st
        assert st["result"]["n_rows"] == 1
        assert client.get("/ingest/status", params={"document_id": r.json()["document_id"]}).json()["status"] == "ingested"
    finally:
        _cleanup(sha256_bytes(data))
