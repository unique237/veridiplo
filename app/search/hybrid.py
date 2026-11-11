"""Hybrid evidence search: Postgres full-text (french + simple) + pgvector cosine (+ pg_trgm on names),
fused with Reciprocal Rank Fusion (RRF, k=60).

    search(session, q, institution=None, year=None, programme=None, doc_type=None, limit=10,
           kinds=("pv_row", "registry_row")) -> list[dict]

Each hit carries provenance (document, page, bbox, page_size, source file), a snippet, the structured row
fields, a crop URL (highlighted by ``GET /search/crop/{chunk_id}.png``) and a score breakdown
``{fts, vector, rrf, trgm, fts_rank, vector_rank, trgm_rank}``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.names import normalize_name
from app.search.embed import embed_query

RRF_K = 60
DEFAULT_KINDS = ("pv_row", "registry_row")
_WORD = re.compile(r"\w+", re.UNICODE)
_trgm_ok: bool | None = None


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def build_tsquery(q: str) -> str:
    """OR-query over the raw and accent-folded tokens (OCR noise tolerant; ranking rewards more matches)."""
    toks: list[str] = []
    for w in _WORD.findall(q or ""):
        w = w.lower()
        if len(w) < 2 and not w.isdigit():
            continue
        for v in (w, _fold(w)):
            v = re.sub(r"[^\w]", "", v)
            if v and v not in toks:
                toks.append(v)
    return " | ".join(toks)


def _filters(institution, year, programme, doc_type, kinds: Sequence[str]) -> tuple[str, dict[str, Any]]:
    where = ["c.kind = ANY(:kinds)"]
    params: dict[str, Any] = {"kinds": list(kinds)}
    if institution:
        where.append("UPPER(c.institution_id) = UPPER(:institution)")
        params["institution"] = str(institution)
    if year is not None:
        where.append("c.session_year = :year")
        params["year"] = int(year)
    if programme:
        where.append("UPPER(c.programme_id) = UPPER(:programme)")
        params["programme"] = str(programme)
    if doc_type:
        where.append("d.doc_type = :doc_type")
        params["doc_type"] = str(doc_type)
    return " AND ".join(where), params


def _has_trgm(session: Session) -> bool:
    global _trgm_ok
    if _trgm_ok is None:
        try:
            _trgm_ok = bool(session.execute(text("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'")).scalar())
        except Exception:
            _trgm_ok = False
    return _trgm_ok


def _vec_literal(v: Iterable[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def search(session: Session, q: str, institution: str | None = None, year: int | None = None,
           programme: str | None = None, doc_type: str | None = None, limit: int = 10,
           kinds: Sequence[str] = DEFAULT_KINDS) -> list[dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return []
    where, params = _filters(institution, year, programme, doc_type, kinds)
    pool = max(60, limit * 6)
    base_from = "FROM chunks c JOIN source_documents d ON d.id = c.document_id"

    # ---- 1. full-text (french + simple configs)
    fts: dict[int, float] = {}
    tsq = build_tsquery(q)
    if tsq:
        rows = session.execute(text(f"""
            SELECT c.id,
                   ts_rank_cd(to_tsvector('simple', coalesce(c.text,'')), to_tsquery('simple', :tsq))
                 + ts_rank_cd(to_tsvector('french', coalesce(c.text,'')), to_tsquery('french', :tsq)) AS r
            {base_from}
            WHERE {where}
              AND (to_tsvector('simple', coalesce(c.text,'')) @@ to_tsquery('simple', :tsq)
                   OR to_tsvector('french', coalesce(c.text,'')) @@ to_tsquery('french', :tsq))
            ORDER BY r DESC LIMIT :pool"""), {**params, "tsq": tsq, "pool": pool}).all()
        fts = {r[0]: float(r[1]) for r in rows}

    # ---- 2. vector cosine
    vec: dict[int, float] = {}
    try:
        qv = _vec_literal(embed_query(q))
        rows = session.execute(text(f"""
            SELECT c.id, 1 - (c.embedding <=> CAST(:qv AS vector)) AS cos
            {base_from}
            WHERE {where} AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:qv AS vector) LIMIT :pool"""), {**params, "qv": qv, "pool": pool}).all()
        vec = {r[0]: float(r[1]) for r in rows}
    except Exception:
        session.rollback()  # e.g. vector extension missing in a unit-test database

    # ---- 3. trigram similarity on the normalised name (typo / OCR tolerant)
    trg: dict[int, float] = {}
    qn = normalize_name(q)
    if qn and _has_trgm(session):
        rows = session.execute(text(f"""
            SELECT c.id, similarity(c.name_norm, :qn) AS s
            {base_from}
            WHERE {where} AND c.name_norm IS NOT NULL AND similarity(c.name_norm, :qn) > 0.3
            ORDER BY s DESC LIMIT :pool"""), {**params, "qn": qn, "pool": pool}).all()
        trg = {r[0]: float(r[1]) for r in rows}

    # ---- 4. Reciprocal Rank Fusion
    fused, ranks = rrf_fuse({"fts": fts, "vector": vec, "trgm": trg})
    top = sorted(fused, key=lambda i: -fused[i])[:limit]
    if not top:
        return []

    rows = session.execute(text("""
        SELECT c.id, c.document_id, c.kind, c.page, c.row_idx, c.text, c.fields, c.bbox, c.page_size, c.ocr_conf,
               c.institution_id, c.programme_id, c.session_year, c.name_norm,
               d.filename, d.sha256, d.doc_type
        FROM chunks c JOIN source_documents d ON d.id = c.document_id WHERE c.id = ANY(:ids)"""),
                           {"ids": top}).mappings().all()
    by_id = {r["id"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for cid in top:
        r = by_id.get(cid)
        if r is None:
            continue
        out.append({
            "chunk_id": cid,
            "document_id": r["document_id"],
            "kind": r["kind"],
            "doc_type": r["doc_type"],
            "snippet": _snippet(r["text"]),
            "page": r["page"],
            "row_idx": r["row_idx"],
            "bbox": list(r["bbox"]) if r["bbox"] is not None else None,
            "page_size": list(r["page_size"]) if r["page_size"] is not None else None,
            "score": round(fused[cid], 6),
            "score_breakdown": {
                "fts": round(fts.get(cid, 0.0), 6),
                "vector": round(vec.get(cid, 0.0), 6),
                "trgm": round(trg.get(cid, 0.0), 6),
                "rrf": round(fused[cid], 6),
                "fts_rank": ranks["fts"].get(cid),
                "vector_rank": ranks["vector"].get(cid),
                "trgm_rank": ranks["trgm"].get(cid),
            },
            "fields": r["fields"] or {},
            "institution_id": r["institution_id"],
            "programme_id": r["programme_id"],
            "session_year": r["session_year"],
            "name_norm": r["name_norm"],
            "ocr_conf": r["ocr_conf"],
            "sha256": r["sha256"],
            "source_file": r["filename"],
            "crop_url": f"/search/crop/{cid}.png",
        })
    return out


def rrf_fuse(channels: dict[str, dict[int, float]], k: int = RRF_K) -> tuple[dict[int, float], dict[str, dict[int, int]]]:
    """Reciprocal Rank Fusion: score(d) = sum over channels of 1 / (k + rank_channel(d)). Higher raw = better rank."""
    ranks: dict[str, dict[int, int]] = {}
    fused: dict[int, float] = {}
    for label, d in channels.items():
        order = sorted(d, key=lambda i: -d[i])
        ranks[label] = {cid: r for r, cid in enumerate(order, start=1)}
        for cid, r in ranks[label].items():
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + r)
    return fused, ranks


def _snippet(t: str | None, n: int = 240) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def list_filters(session: Session) -> dict[str, Any]:
    """Distinct institutions / years / programmes of the indexed archive (UI drop-downs)."""
    rows = session.execute(text("""
        SELECT DISTINCT institution_id, programme_id, session_year FROM chunks
        WHERE kind IN ('pv_row','registry_row')""")).all()
    inst = sorted({r[0] for r in rows if r[0]})
    years = sorted({r[2] for r in rows if r[2] is not None})
    progs = sorted({(r[0] or "", r[1]) for r in rows if r[1]})
    return {
        "institutions": inst,
        "years": years,
        "programmes": sorted({p for _, p in progs}),
        "programmes_by_institution": {i: sorted({p for ii, p in progs if ii == i}) for i in inst},
    }
