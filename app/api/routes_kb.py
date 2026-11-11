"""/kb and /gaps routes: policy knowledge-base search, rules (with justification chunks), sources, gap register."""
from __future__ import annotations

import logging
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import GapRecord, KBChunk
from app.policy import loader
from app.policy.engine import chunks_by_ids

log = logging.getLogger("veridiplo.kb")
router = APIRouter(tags=["kb"])


def _chunk_dict(c: KBChunk, snippet_chars: Optional[int] = 600) -> dict:
    d = {k: getattr(c, k) for k in ("chunk_id", "source_id", "title", "issuer", "date", "jurisdiction", "binding", "kind",
                                    "article", "as_reported", "lang", "topics", "pipeline_nodes", "readiness_dims", "url", "sha256")}
    d["text"] = c.text if snippet_chars is None else c.text[:snippet_chars]
    return d


@router.get("/kb/search")
def kb_search(q: str, limit: int = 10, source_id: Optional[str] = None, binding: Optional[bool] = None,
              db: Session = Depends(get_db)):
    """Full-text search (Postgres FTS, `simple` config) over kb_chunks with BM25-like file fallback."""
    results: list[dict] = []
    try:
        sql = ("SELECT chunk_id, ts_rank(to_tsvector('simple', text || ' ' || coalesce(article,'')), q) AS score "
               "FROM kb_chunks, websearch_to_tsquery('simple', :q) q "
               "WHERE to_tsvector('simple', text || ' ' || coalesce(article,'')) @@ q "
               + ("AND source_id = :sid " if source_id else "") + ("AND binding = :b " if binding is not None else "")
               + "ORDER BY score DESC LIMIT :n")
        rows = db.execute(text(sql), {"q": q, "sid": source_id, "b": binding, "n": limit}).all()
        by_id = {c.chunk_id: c for c in db.execute(select(KBChunk).where(KBChunk.chunk_id.in_([r[0] for r in rows]))).scalars()}
        for cid, score in rows:
            d = _chunk_dict(by_id[cid])
            d["score"] = round(float(score), 4)
            results.append(d)
    except Exception:
        db.rollback()
        log.warning("kb_chunks FTS failed; falling back to file search", exc_info=True)
    if not results:
        for c in loader.search_chunks(q, limit=limit, source_id=source_id):
            if binding is not None and bool(c.get("binding")) != binding:
                continue
            c = dict(c)
            c["text"] = c["text"][:600]
            results.append(c)
    return {"q": q, "count": len(results), "results": results}


@router.get("/kb/chunks/{chunk_id:path}")
def kb_chunk(chunk_id: str, db: Session = Depends(get_db)):
    cid = loader.resolve_ref(chunk_id) or chunk_id
    c = db.get(KBChunk, cid)
    if c is not None:
        return _chunk_dict(c, None)
    f = loader.get_chunk(chunk_id)
    if f is None:
        raise HTTPException(404, "chunk not found")
    return f


@router.get("/kb/rules")
def kb_rules(with_text: bool = False, checkpoint: Optional[str] = None, db: Session = Depends(get_db)):
    out = []
    for r in loader.load_rules():
        if checkpoint and r["checkpoint"] != checkpoint:
            continue
        d = {k: v for k, v in r.items() if not k.startswith("_")}
        d["justification"] = chunks_by_ids([c for c in r.get("justified_chunks", []) if c], db if with_text else None,
                                            2000 if with_text else 0)
        if not with_text:
            for j in d["justification"]:
                j.pop("text", None)
        out.append(d)
    return {"count": len(out), "rules": out}


@router.get("/kb/sources")
def kb_sources():
    import json
    status = {}
    p = loader.kb_dir() / "chunks" / "_status.json"
    if p.exists():
        status = json.loads(p.read_text(encoding="utf-8")).get("sources", {})
    out = []
    for s in loader.load_sources():
        st = status.get(s["id"], {})
        out.append({**{k: s.get(k) for k in ("id", "title", "issuer", "date", "jurisdiction", "binding", "kind", "url", "status")},
                    "chunks": st.get("chunks"), "build_status": st.get("status")})
    return {"count": len(out), "sources": out}


# ------------------------------------------------------------------------------------------------ gaps
def _gap_dict(g: GapRecord) -> dict:
    return {"id": g.id, "gap_id": g.gap_id, "type": g.type, "title": g.title, "detail": g.detail, "case_id": g.case_id,
            "scenario_id": g.scenario_id, "checkpoint": g.checkpoint, "created_at": g.created_at.isoformat() if g.created_at else None}


@router.get("/gaps")
def gaps(case_id: Optional[str] = None, runtime: Optional[bool] = None, type: Optional[str] = None, db: Session = Depends(get_db)):
    q = select(GapRecord).order_by(GapRecord.id)
    if case_id:
        q = q.where(GapRecord.case_id == case_id)
    if runtime is True:
        q = q.where(GapRecord.case_id.is_not(None))
    elif runtime is False:
        q = q.where(GapRecord.case_id.is_(None))
    if type:
        q = q.where(GapRecord.type == type)
    rows = [_gap_dict(g) for g in db.execute(q).scalars()]
    return {"count": len(rows), "gaps": rows}


@router.get("/gaps/export.md")
def gaps_export(db: Session = Depends(get_db)):
    seeds = db.execute(select(GapRecord).where(GapRecord.case_id.is_(None)).order_by(GapRecord.id)).scalars().all()
    runtime = db.execute(select(GapRecord).where(GapRecord.case_id.is_not(None)).order_by(GapRecord.id)).scalars().all()
    lines = ["# VeriDiplo - Gap register", "", "_SPECIMEN - SYNTHETIC DATA. Generated from the `gaps` table._", ""]
    for kind, title in (("policy", "Policy gaps"), ("standard", "Standards gaps")):
        lines += [f"## {title}", ""]
        for g in [x for x in seeds if x.type == kind]:
            d = g.detail or {}
            lines += [f"### {g.gap_id} - {g.title}", ""]
            if d.get("finding"):
                lines.append(f"- **Finding:** {d['finding']}")
            if d.get("surfaced_by"):
                lines.append(f"- **Surfaced by:** {', '.join(d['surfaced_by'])}")
            if d.get("proposal"):
                lines.append(f"- **Proposal:** {d['proposal']}")
            if d.get("comparison"):
                lines.append(f"- **Comparison:** {d['comparison']}")
            if d.get("sources"):
                lines.append(f"- **Sources:** {', '.join(d['sources'])}")
            lines.append("")
    lines += ["## Runtime gaps detected by the GapAnalystAgent", "", "| Gap | Case | Checkpoint | Kind | Reason |", "|---|---|---|---|---|"]
    for g in runtime:
        d = g.detail or {}
        lines.append(f"| {g.gap_id} | {(g.case_id or '')[:8]} | {g.checkpoint or ''} | {d.get('kind', '')} | "
                     f"{str(d.get('reason', g.title)).replace('|', '/')[:140]} |")
    return Response("\n".join(lines) + "\n", media_type="text/markdown; charset=utf-8")
