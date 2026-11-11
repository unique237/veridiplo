"""IntakeAgent (Y.3172 SRC -> C): accept archive documents, hash, provenance, dedupe, hand to OCR/registry parsing.

``ingest_file`` is idempotent: the SHA-256 is the identity of a document; re-ingesting identical bytes returns the
existing ``SourceDocument``.  Supported: PDF, TIFF, PNG, JPG (scans, OCR'd), CSV, XLSX (registries, parsed).
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents import ocr as ocr_mod
from app.agents import registry as registry_mod
from app.core import refdata
from app.core.hashing import sha256_file, sha256_obj
from app.core.models import Chunk, SourceDocument

log = logging.getLogger("veridiplo.intake")

SUPPORTED_EXTS = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".csv", ".xlsx", ".xls"}
SPREADSHEET_EXTS = {".csv", ".xlsx", ".xls"}
DOC_TYPES = {"pv", "registry", "list", "diploma", "other"}
EMBED_BATCH = 64

_PV_NAME = re.compile(r"^(?P<inst>[A-Z]{2,8})_(?P<prog>[A-Z0-9]{2,12})_(?P<year>(?:19|20)\d{2})(?:\.clean)?$")
_INST_YEAR = re.compile(r"^(?P<inst>[A-Z]{2,8})_(?P<year>(?:19|20)\d{2})(?:\.clean)?$")
_DIR_TYPES = {"pv": "pv", "pvs": "pv", "registries": "registry", "registry": "registry", "lists": "list",
              "list": "list", "diplomas": "diploma", "diploma": "diploma"}


class IngestBlocked(Exception):
    """Raised when the before_ingest policy checkpoint blocks a document."""

    def __init__(self, message: str, rule_ids: list[str] | None = None):
        super().__init__(message)
        self.rule_ids = rule_ids or []


def infer_from_filename(path: str | Path) -> dict[str, Any]:
    """Provenance hints from ``<INST>_<PROGRAMME>_<YEAR>.pdf`` / ``<INST>_<YEAR>.csv|xlsx|pdf`` (+ parent dir)."""
    p = Path(path)
    stem, ext = p.stem, p.suffix.lower()
    out: dict[str, Any] = {}
    m = _PV_NAME.match(stem)
    m2 = None if m else _INST_YEAR.match(stem)
    if m:
        out.update(issuer=m["inst"], programme_id=m["prog"], session_year=int(m["year"]), doc_type="pv")
    elif m2:
        out.update(issuer=m2["inst"], session_year=int(m2["year"]))
        out["doc_type"] = "registry" if ext in SPREADSHEET_EXTS else "list"
    d = _DIR_TYPES.get(p.parent.name.lower())
    if d and "doc_type" not in out:
        out["doc_type"] = d
    if "issuer" in out and refdata.institutions() and refdata.get_institution(out["issuer"]) is None:
        out.pop("issuer")  # not a known institution id: do not trust the pattern
        out.pop("programme_id", None)
    return out


def _policy_before_ingest(info: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """before_ingest checkpoint. Returns (action, rule_ids, kb_chunk_ids). Allows when the engine is unavailable."""
    try:
        from app.core.config import get_settings

        if get_settings().POLICY_MODE != "on":
            return "allow", [], []
        from app.policy.engine import get_engine, merge_context

        res = get_engine().evaluate("before_ingest", merge_context({"upload": {"age_days": 0}}))
        return res.action, list(res.rule_ids), list(res.kb_chunk_ids)
    except Exception as exc:  # engine/rules not available yet -> stub allows
        log.debug("before_ingest checkpoint unavailable (%s): allow", exc)
        return "allow", [], []


def _audit(session: Session, action: str, input_hash: str | None, output_hash: str | None,
           rule_ids: Iterable[str] = (), kb_ids: Iterable[str] = (), detail: dict | None = None) -> None:
    try:
        from app.core.audit import log_event
    except Exception:  # pragma: no cover - audit module not present
        return
    try:
        from app.core.config import get_settings

        log_event(session, None, "IntakeAgent", action, input_hash=input_hash, output_hash=output_hash,
                  policy_rule_ids=list(rule_ids), kb_chunk_ids=list(kb_ids),
                  policy_mode=get_settings().POLICY_MODE, actor="agent", detail=detail or {})
    except Exception:
        log.exception("audit event failed")


def embed_chunks(session: Session, chunks: list[Chunk]) -> int:
    """Fill ``Chunk.embedding`` (batches). Silently skipped when the embedding module is unavailable."""
    try:
        from app.search.embed import embed_passages
    except Exception as exc:
        log.warning("embeddings unavailable (%s): chunks stored without vectors", exc)
        return 0
    n = 0
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        try:
            vecs = embed_passages([c.text for c in batch])
        except Exception as exc:
            log.warning("embedding batch failed (%s)", exc)
            return n
        for c, v in zip(batch, vecs):
            c.embedding = v
        n += len(batch)
    return n


class IntakeAgent:
    name = "IntakeAgent"
    node = "C"

    def __init__(self, workers: int = 1, embed: bool = True):
        self.workers = workers
        self.embed = embed

    # ------------------------------------------------------------------ public API
    def ingest_file(self, path: str, issuer: Optional[str] = None, doc_type: Optional[str] = None,
                    session_year: Optional[int] = None, programme_id: Optional[str] = None,
                    session: Optional[Session] = None) -> SourceDocument:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(path)
        if p.suffix.lower() not in SUPPORTED_EXTS:
            raise ValueError(f"unsupported file type {p.suffix!r} (supported: {sorted(SUPPORTED_EXTS)})")
        own = session is None
        if own:
            from app.core.db import SessionLocal

            session = SessionLocal()
        assert session is not None
        try:
            return self._ingest(session, p, issuer, doc_type, session_year, programme_id)
        except Exception:
            session.rollback()
            raise
        finally:
            if own:
                session.close()

    def ingest_directory(self, root: str, workers: int | None = None, **kw: Any) -> list[SourceDocument]:
        """Ingest every supported archive file under ``root`` (skips ``*.clean.pdf`` test twins, JSON, diplomas)."""
        files = sorted(self.iter_archive_files(root))
        workers = workers or self.workers
        pdfs = [f for f in files if f.suffix.lower() not in SPREADSHEET_EXTS]
        if pdfs:
            ocr_mod.ocr_many(pdfs, workers=workers, progress=True)  # warm the OCR cache in parallel
        docs = []
        for f in files:
            try:
                docs.append(self.ingest_file(str(f), **kw))
            except IngestBlocked as exc:
                log.warning("blocked %s: %s", f, exc)
            except Exception:
                log.exception("ingest failed for %s", f)
        return docs

    @staticmethod
    def iter_archive_files(root: str | Path) -> Iterable[Path]:
        for f in Path(root).rglob("*"):
            if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTS:
                continue
            if f.name.endswith(".clean.pdf") or f.parent.name.lower() in ("diplomas", "cache"):
                continue
            yield f

    # ------------------------------------------------------------------ internals
    def _ingest(self, session: Session, p: Path, issuer, doc_type, session_year, programme_id) -> SourceDocument:
        t0 = time.time()
        sha = sha256_file(p)
        existing = session.execute(select(SourceDocument).where(SourceDocument.sha256 == sha)).scalar_one_or_none()
        if existing is not None and existing.status == "ingested":
            _audit(session, "ingest:duplicate", sha, sha, detail={"document_id": existing.id, "filename": p.name})
            session.commit()
            return existing

        hints = infer_from_filename(p)
        issuer = issuer or hints.get("issuer")
        doc_type = doc_type or hints.get("doc_type")
        session_year = session_year or hints.get("session_year")
        programme_id = programme_id or hints.get("programme_id")
        if doc_type and doc_type not in DOC_TYPES:
            raise ValueError(f"unknown doc_type {doc_type!r}")

        action, rule_ids, kb_ids = _policy_before_ingest({"filename": p.name, "doc_type": doc_type})
        if action == "block":
            _audit(session, "ingest:blocked", sha, None, rule_ids, kb_ids, {"filename": p.name})
            session.commit()
            raise IngestBlocked(f"before_ingest blocked {p.name}", rule_ids)

        try:
            rel = str(p.resolve().relative_to(ocr_mod.data_dir().parent.resolve()))
        except ValueError:
            rel = str(p)
        if existing is None:
            doc = SourceDocument(sha256=sha, filename=p.name, path=rel, doc_type=doc_type or "other", issuer=issuer,
                                 programme_id=programme_id, session_year=session_year, status="processing", meta={})
            session.add(doc)
        else:  # queued/failed/processing: (re)process
            doc = existing
            doc.status = "processing"
            session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        session.flush()

        try:
            self._process(session, doc, p, issuer, doc_type, session_year, programme_id)
            doc.status = "ingested"
        except Exception as exc:
            log.exception("processing failed: %s", p)
            doc.status = "failed"
            doc.meta = {**(doc.meta or {}), "error": f"{type(exc).__name__}: {exc}"[:500]}
            session.commit()
            raise
        n_rows = (doc.meta or {}).get("n_rows", 0)
        _audit(session, "ingest", sha, sha256_obj({"document_id": doc.id, "chunks": (doc.meta or {}).get("n_chunks"),
                                                    "rows": n_rows}), rule_ids, kb_ids,
               {"document_id": doc.id, "filename": p.name, "doc_type": doc.doc_type, "issuer": doc.issuer,
                "session_year": doc.session_year, "programme_id": doc.programme_id, "pages": doc.pages,
                "n_rows": n_rows, "ms": int((time.time() - t0) * 1000)})
        session.commit()
        return doc

    def _process(self, session: Session, doc: SourceDocument, p: Path, issuer, doc_type, session_year,
                 programme_id) -> None:
        ext = p.suffix.lower()
        meta: dict[str, Any] = dict(doc.meta or {})
        chunk_dicts: list[dict[str, Any]]
        if ext in SPREADSHEET_EXTS:
            doc.doc_type = doc_type or "registry"
            chunk_dicts = registry_mod.build_registry_chunks(p, issuer, session_year)
            doc.pages = 1
            meta["format"] = ext.lstrip(".")
        else:
            ocr = ocr_mod.ocr_document(p, sha=doc.sha256, workers=self.workers)
            doc.pages = len(ocr["pages"])
            doc.ocr_confidence = ocr.get("conf")
            header = ocr_mod.parse_pv_header(ocr["pages"])
            meta["engine"] = (ocr["pages"][0]["engine"] if ocr["pages"] else None)
            meta["header"] = {k: v for k, v in header.items() if k not in ("is_pv", "is_list")}
            # provenance inferred from the PV preamble when the caller / filename did not tell us
            if not doc_type:
                doc_type = "pv" if header.get("is_pv") else "list" if header.get("is_list") else None
            issuer = issuer or header.get("institution_id")
            programme_id = programme_id or header.get("programme_id")
            session_year = session_year or header.get("session_year")
            for k, v in (("institution_id", issuer), ("programme_id", programme_id), ("session_year", session_year)):
                hv = header.get(k)
                if hv and v and hv != v:
                    meta.setdefault("header_mismatch", {})[k] = {"declared": v, "header": hv}
            if doc_type == "pv" or (doc_type is None):
                chunk_dicts = ocr_mod.build_pv_chunks(ocr, issuer, programme_id, session_year, header)
                if doc_type is None:
                    doc_type = "pv" if any(c["kind"] == "pv_row" for c in chunk_dicts) else "other"
            elif doc_type == "list":
                chunk_dicts = ocr_mod.build_list_chunks(ocr, issuer, session_year)
            else:
                chunk_dicts = ocr_mod.build_text_chunks(ocr, refdata.context_line(issuer, programme_id, session_year))
            doc.doc_type = doc_type or "other"
        doc.issuer, doc.programme_id, doc.session_year = issuer, programme_id, session_year
        chunks = []
        for d in chunk_dicts:
            d = dict(d)
            d.setdefault("institution_id", issuer)
            if d.get("programme_id") is None:
                d["programme_id"] = programme_id
            if d.get("session_year") is None:
                d["session_year"] = session_year
            chunks.append(Chunk(document_id=doc.id, **d))
        session.add_all(chunks)
        session.flush()
        if self.embed:
            meta["embedded"] = embed_chunks(session, chunks)
        meta["n_chunks"] = len(chunks)
        meta["n_rows"] = sum(1 for c in chunks if c.kind != "text")
        doc.meta = meta
