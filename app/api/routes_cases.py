"""/cases routes: create (JSON or multipart), run (sync or background), get, list, review, report (JSON/PDF)."""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import Orchestrator
from app.agents.reporter import ReporterAgent
from app.agents.sims import RegistrarSimAgent
from app.core.audit import log_event
from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.core.hashing import sha256_bytes
from app.core.models import Case
from app.core.schemas import CaseRequest, DiplomaFields, ReviewIn
from app.policy.engine import chunks_by_ids

log = logging.getLogger("veridiplo.cases")
router = APIRouter(tags=["cases"])

_orch: Optional[Orchestrator] = None


def orchestrator() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch


def case_view(c: Case) -> dict[str, Any]:
    d = dict(c.result or {"case_id": c.id, "status": c.status, "steps": []})
    d.update(case_id=c.id, status=c.status, review_state=c.review_state, review=c.review, scenario_id=c.scenario_id,
             created_at=c.created_at.isoformat() if c.created_at else None,
             request={k: v for k, v in (c.request or {}).items() if k != "items"},
             diploma_fields=c.diploma_fields)
    return d


def create_case(session: Session, request: dict[str, Any], diploma_path: Optional[str] = None,
                scenario_id: Optional[str] = None, actor: str = "human") -> Case:
    cid = str(uuid.uuid4())
    case = Case(id=cid, status="created", request=request, diploma_path=diploma_path, scenario_id=scenario_id or request.get("scenario_id"))
    session.add(case)
    session.flush()
    log_event(session, cid, "API", "case_created", policy_mode=None, actor=actor,
              detail={"purpose": request.get("purpose"), "requester_type": request.get("requester_type"),
                      "requester_country": request.get("requester_country"), "has_file": bool(diploma_path),
                      "consent_token_present": bool(request.get("consent_token")), "batch_size": request.get("batch_size", 1)})
    session.commit()
    return case


@router.post("/cases")
async def post_case(request: Request, db: Session = Depends(get_db)):
    ctype = request.headers.get("content-type", "")
    path: Optional[str] = None
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        data: dict[str, Any] = {}
        for k in ("consent_token", "legal_basis", "purpose", "requester_type", "requester_country", "verifier_id", "lang",
                  "diploma_case_id", "scenario_id"):
            v = form.get(k)
            if v not in (None, ""):
                data[k] = str(v)
        if form.get("batch_size"):
            data["batch_size"] = int(form["batch_size"])
        if form.get("fields"):
            data["fields"] = DiplomaFields(**json.loads(str(form["fields"]))).model_dump()
        up = form.get("diploma") or form.get("file")
        if up is not None and hasattr(up, "read"):
            content = await up.read()
            if content:
                cid_dir = Path(get_settings().DATA_DIR) / "uploads" / uuid.uuid4().hex[:12]
                cid_dir.mkdir(parents=True, exist_ok=True)
                fp = cid_dir / Path(up.filename or "diploma.bin").name
                fp.write_bytes(content)
                path = str(fp)
                data["use_typed_fields"] = False
                data["upload_sha256"] = sha256_bytes(content)
        req = CaseRequest(**data).model_dump()
        req.update({k: data[k] for k in ("use_typed_fields", "upload_sha256") if k in data})
    else:
        body = await request.json()
        extra = {k: body[k] for k in ("items", "use_typed_fields", "channel") if k in body}
        req = CaseRequest(**{k: v for k, v in body.items() if k in CaseRequest.model_fields}).model_dump()
        req.update(extra)
    if not (req.get("fields") or req.get("diploma_case_id") or path or req.get("items") or req.get("batch_size", 1) > 1):
        raise HTTPException(422, "provide diploma fields, a diploma file, or diploma_case_id")
    case = create_case(db, req, path)
    return {"case_id": case.id, "status": case.status}


def _run_background(case_id: str, mode: Optional[str]) -> None:
    s = SessionLocal()
    try:
        orchestrator().run_case(s, case_id, mode)
    except Exception:
        log.exception("background run failed")
    finally:
        s.close()


@router.post("/cases/{case_id}/run")
def run_case(case_id: str, background: BackgroundTasks, policy_mode: Optional[str] = None, wait: bool = True,
             db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    if policy_mode not in (None, "on", "off"):
        raise HTTPException(422, "policy_mode must be on|off")
    if wait:
        try:
            res = orchestrator().run_case(db, case_id, policy_mode)
        except FileNotFoundError as exc:
            raise HTTPException(422, str(exc))
        return json.loads(res.model_dump_json())
    orchestrator().start(db, case_id, policy_mode)
    background.add_task(_run_background, case_id, policy_mode)
    return {"case_id": case_id, "status": "running"}


@router.get("/cases")
def list_cases(status: Optional[str] = None, review_state: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)):
    q = select(Case).order_by(Case.created_at.desc()).limit(limit)
    if status:
        q = q.where(Case.status == status)
    if review_state:
        q = q.where(Case.review_state == review_state)
    out = []
    for c in db.execute(q).scalars():
        out.append({"case_id": c.id, "status": c.status, "verdict": c.verdict, "policy_mode": c.policy_mode,
                    "review_state": c.review_state, "scenario_id": c.scenario_id, "created_at": c.created_at.isoformat(),
                    "holder": (c.diploma_fields or {}).get("holder_name"),
                    "institution_id": (c.diploma_fields or {}).get("institution_id")})
    return out


@router.get("/cases/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db)):
    c = db.get(Case, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return case_view(c)


@router.post("/cases/{case_id}/review")
def review_case(case_id: str, body: ReviewIn, simulate: bool = False, db: Session = Depends(get_db)):
    c = db.get(Case, case_id)
    if c is None or not c.result:
        raise HTTPException(404, "case not found or not run yet")
    out = orchestrator().review(db, case_id, body, actor="agent" if simulate else "human")
    if not out["accepted"]:
        return Response(content=json.dumps(out, default=str), status_code=422, media_type="application/json")
    return out


def _pack(db: Session, case_id: str, audience: str) -> dict[str, Any]:
    c = db.get(Case, case_id)
    if c is None or not c.result:
        raise HTTPException(404, "case not found or not run yet")
    res = c.result
    if audience == "verifier" and res["policy_mode"] == "on" and res["status"] in ("pending_review", "blocked"):
        raise HTTPException(409, "result held for registrar review or blocked; use audience=registrar")
    rep = ReporterAgent()
    texts = {}
    for f in res.get("fired_rules", []):
        for ch in chunks_by_ids(f.get("kb_chunk_ids", []), db, 300):
            texts[ch["chunk_id"]] = ch
    return rep.build_pack(res, audience=audience if res["policy_mode"] == "on" else "registrar", policy_texts=texts)


@router.get("/cases/{case_id}/report.json")
def report_json(case_id: str, audience: str = "registrar", db: Session = Depends(get_db)):
    return _pack(db, case_id, audience)


@router.get("/cases/{case_id}/report.pdf")
def report_pdf(case_id: str, audience: str = "registrar", lang: str = "fr", db: Session = Depends(get_db)):
    pack = _pack(db, case_id, audience)
    try:
        pdf = ReporterAgent().render_pdf(pack, lang)
    except ImportError:
        raise HTTPException(501, "PDF rendering needs reportlab (not installed); use /report.json")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="veridiplo-{case_id[:8]}.pdf"',
                             "X-Report-SHA256": pack["report_sha256"]})
