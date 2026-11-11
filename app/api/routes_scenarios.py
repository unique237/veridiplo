"""/scenarios routes: list S1-S7 and run one in Version A and B with a side-by-side diff."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents.scenario_runner import list_scenarios, load_scenario, run_scenario
from app.api.routes_cases import orchestrator
from app.core.db import get_db

router = APIRouter(tags=["scenarios"])


@router.get("/scenarios")
def scenarios():
    return {"scenarios": list_scenarios()}


@router.get("/scenarios/{sid}")
def scenario(sid: str):
    try:
        return load_scenario(sid)
    except FileNotFoundError:
        raise HTTPException(404, "unknown scenario")


@router.post("/scenarios/{sid}/run")
def run(sid: str, db: Session = Depends(get_db)):
    try:
        return run_scenario(db, sid, orchestrator())
    except FileNotFoundError:
        raise HTTPException(404, "unknown scenario")
