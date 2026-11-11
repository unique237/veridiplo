"""POST /ingest (upload archive documents for an issuer) and GET /ingest/status."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.intake import DOC_TYPES, SUPPORTED_EXTS, IngestBlocked, IntakeAgent
from app.core.db import get_db
from app.core.hashing import sha256_bytes
from app.core.models import Chunk, Job, SourceDocument

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _upload_dir() -> Path:
    from app.agents.ocr import data_dir

    p = data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _doc_out(d: SourceDocument) -> dict:
    return {"document_id": d.id, "sha256": d.sha256, "filename": d.filename, "doc_type": d.doc_type, "issuer": d.issuer,
            "programme_id": d.programme_id, "session_year": d.session_year, "pages": d.pages, "status": d.status,
            "ocr_confidence": d.ocr_confidence, "n_chunks": (d.meta or {}).get("n_chunks"),
            "n_rows": (d.meta or {}).get("n_rows"), "error": (d.meta or {}).get("error")}


@router.post("")
async def ingest(file: UploadFile = File(...), issuer: Optional[str] = Form(None), doc_type: Optional[str] = Form(None),
                 session_year: Optional[int] = Form(None), programme_id: Optional[str] = Form(None),
                 sync: bool = False, db: Session = Depends(get_db)):
    """Upload a PV scan / registry / list. ``?sync=true`` processes immediately, otherwise a job is queued."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename or "upload").name)
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(415, f"unsupported file type {ext!r}; supported: {sorted(SUPPORTED_EXTS)}")
    if doc_type and doc_type not in DOC_TYPES:
        raise HTTPException(422, f"doc_type must be one of {sorted(DOC_TYPES)}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    sha = sha256_bytes(data)
    existing = db.execute(select(SourceDocument).where(SourceDocument.sha256 == sha)).scalar_one_or_none()
    if existing is not None and existing.status == "ingested":
        return {"status": "duplicate", "duplicate": True, **_doc_out(existing)}

    dest = _upload_dir() / sha[:12] / name  # original filename kept (provenance is inferred from it)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if sync:
        try:
            doc = IntakeAgent(workers=1).ingest_file(str(dest), issuer=issuer, doc_type=doc_type,
                                                     session_year=session_year, programme_id=programme_id, session=db)
        except IngestBlocked as exc:
            raise HTTPException(403, {"blocked": True, "message": str(exc), "rule_ids": exc.rule_ids})
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"status": doc.status, "duplicate": False, **_doc_out(doc)}

    if existing is None:
        from app.agents.ocr import data_dir

        try:
            rel = str(dest.resolve().relative_to(data_dir().parent.resolve()))
        except ValueError:
            rel = str(dest)
        existing = SourceDocument(sha256=sha, filename=name, path=rel, doc_type=doc_type or "other", issuer=issuer,
                                  programme_id=programme_id, session_year=session_year, status="queued", meta={})
        db.add(existing)
        db.flush()
    else:
        existing.status = "queued"
    job = Job(kind="ingest", status="queued",
              payload={"path": str(dest), "issuer": issuer, "doc_type": doc_type, "session_year": session_year,
                       "programme_id": programme_id, "document_id": existing.id})
    db.add(job)
    db.commit()
    return {"status": "queued", "duplicate": False, "job_id": job.id, **_doc_out(existing)}


@router.get("/status")
def ingest_status(job_id: Optional[int] = None, document_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Job / document status, or global corpus counters when no id is given."""
    if job_id is not None:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return {"job_id": job.id, "kind": job.kind, "status": job.status, "error": job.error,
                "result": (job.payload or {}).get("result"), "finished_at": job.finished_at}
    if document_id is not None:
        d = db.get(SourceDocument, document_id)
        if d is None:
            raise HTTPException(404, "document not found")
        return _doc_out(d)
    docs = dict(db.execute(select(SourceDocument.status, func.count()).group_by(SourceDocument.status)).all())
    types = dict(db.execute(select(SourceDocument.doc_type, func.count()).group_by(SourceDocument.doc_type)).all())
    chunks = dict(db.execute(select(Chunk.kind, func.count()).group_by(Chunk.kind)).all())
    jobs = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    embedded = db.execute(select(func.count()).select_from(Chunk).where(Chunk.embedding.is_not(None))).scalar_one()
    return {"documents": {"by_status": docs, "by_type": types, "total": sum(docs.values())},
            "chunks": {"by_kind": chunks, "total": sum(chunks.values()), "embedded": embedded},
            "jobs": jobs}
