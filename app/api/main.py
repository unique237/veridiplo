"""VeriDiplo FastAPI entrypoint.

Routers are auto-loaded: any module among ROUTER_MODULES that exists and exposes
`router` is included, so other agents add routes without editing this file.
"""
from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import get_settings

log = logging.getLogger("veridiplo.api")

ROUTER_MODULES = [
    "routes_cases",
    "routes_search",
    "routes_kb",
    "routes_audit",
    "routes_scenarios",
    "routes_ingest",
]

DB_STATE: dict = {"init": "pending"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db = importlib.import_module("app.core.db")  # lazy: /health works even if missing
        db.init_db()
        DB_STATE["init"] = "ok"
    except ModuleNotFoundError as exc:
        if exc.name == "app.core.db":
            DB_STATE["init"] = "skipped (app.core.db missing)"
        else:
            DB_STATE["init"] = f"error: {exc}"
            log.exception("init_db failed")
    except Exception as exc:  # never block startup
        DB_STATE["init"] = f"error: {exc}"
        log.exception("init_db failed")
    yield


app = FastAPI(
    title="VeriDiplo API",
    description="Evidence search and diploma consistency verification (SYNTHETIC DATA).",
    version="0.1.0",
    lifespan=lifespan,
)


def _load_routers(application: FastAPI) -> list[str]:
    loaded = []
    for name in ROUTER_MODULES:
        full = f"app.api.{name}"
        try:
            mod = importlib.import_module(full)
        except ModuleNotFoundError as exc:
            if exc.name == full:
                continue  # module not written yet
            log.exception("router %s failed to import (missing dependency)", full)
            continue
        except Exception:
            log.exception("router %s failed to import", full)
            continue
        router = getattr(mod, "router", None)
        if router is not None:
            application.include_router(router)
            loaded.append(name)
    return loaded


LOADED_ROUTERS = _load_routers(app)


def _check_db() -> str:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(get_settings().DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"[:200]


def _check_kg() -> str:
    try:
        r = httpx.get(f"{get_settings().KG_URL}/query", params={"query": "ASK {}"},
                      headers={"Accept": "application/sparql-results+json"}, timeout=3)
        return "ok" if r.status_code == 200 else f"error: HTTP {r.status_code}"
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"[:200]


@app.get("/health", tags=["meta"])
def health():
    s = get_settings()
    db, kg = _check_db(), _check_kg()
    ok = db == "ok" and kg == "ok"
    from fastapi.responses import JSONResponse

    body = {
        "status": "ok" if ok else "degraded",
        "db": db,
        "kg": kg,
        "init_db": DB_STATE["init"],
        "policy_mode": s.POLICY_MODE,
        "llm_provider": s.LLM_PROVIDER,
        "routers": LOADED_ROUTERS,
    }
    return JSONResponse(body, status_code=200 if ok else 503)
