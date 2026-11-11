"""Evidence search routes: GET /search, GET /search/filters, GET /search/crop/{chunk_id}.png."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import Chunk
from app.search import hybrid

router = APIRouter()


@router.get("/search", tags=["search"])
def search_evidence(q: str = Query(..., min_length=1, description="free text or holder name"),
                    institution: Optional[str] = None, year: Optional[int] = None,
                    programme: Optional[str] = None, doc_type: Optional[str] = Query(None, description="pv | registry"),
                    limit: int = Query(10, ge=1, le=50),
                    db: Session = Depends(get_db)) -> dict:
    """Hybrid (full-text + vector, RRF) search over archive rows with page/bbox provenance and crop URLs."""
    kinds = hybrid.DEFAULT_KINDS
    if doc_type in ("pv", "pv_row"):
        kinds, doc_type = ("pv_row",), None
    elif doc_type in ("registry", "registry_row"):
        kinds, doc_type = ("registry_row",), None
    t0 = time.perf_counter()
    results = hybrid.search(db, q, institution=institution, year=year, programme=programme,
                            doc_type=doc_type, limit=limit, kinds=kinds)
    return {"query": q, "count": len(results), "took_ms": round((time.perf_counter() - t0) * 1000, 1),
            "filters": {"institution": institution, "year": year, "programme": programme},
            "results": results}


@router.get("/search/filters", tags=["search"])
def search_filters(db: Session = Depends(get_db)) -> dict:
    """Distinct institutions / years / programmes (UI drop-downs)."""
    return hybrid.list_filters(db)


@router.get("/search/crop/{chunk_id}.png", tags=["search"])
def search_crop(chunk_id: int, highlight: bool = True, pad: int = 12, db: Session = Depends(get_db)) -> Response:
    """PNG crop of the page area of a chunk, with the matching row highlighted."""
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(404, "chunk not found")
    if not chunk.bbox:
        raise HTTPException(404, "chunk has no bounding box (no image crop available)")
    try:
        from app.agents.ocr import render_crop
    except ImportError as exc:  # ingestion module not deployed
        raise HTTPException(501, f"crop renderer unavailable: {exc}")
    try:
        png = render_crop(chunk.document_id, chunk.page, list(chunk.bbox), highlight=highlight, pad=pad)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        raise HTTPException(404, f"no page image for this chunk: {exc}")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
