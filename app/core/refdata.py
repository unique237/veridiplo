"""Institution / programme reference data (institutions.json) with fuzzy name matching.

Reference data only (names, programmes, signatories) - never the per-person ground truth.
Looked up in $DATA_DIR/synthetic/, then $DATA_DIR/sample/.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from unidecode import unidecode


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[2] / "data")


@lru_cache(maxsize=2)
def _load(data: str) -> list[dict[str, Any]]:
    for sub in ("synthetic", "sample", ""):
        p = Path(data) / sub / "institutions.json"
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
            return obj if isinstance(obj, list) else obj.get("institutions", [])
    return []


def institutions() -> list[dict[str, Any]]:
    return _load(str(data_dir()))


def clear_cache() -> None:
    _load.cache_clear()


def _k(s: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unidecode(str(s or "")).lower()).split())


def get_institution(iid: str | None) -> dict[str, Any] | None:
    for i in institutions():
        if i["id"] == iid:
            return i
    return None


def get_programme(pid: str | None, institution_id: str | None = None) -> dict[str, Any] | None:
    for i in institutions():
        if institution_id and i["id"] != institution_id:
            continue
        for p in i.get("programmes", []):
            if p["id"] == pid:
                return {**p, "institution_id": i["id"]}
    return None


def match_institution(text: str | None, min_score: float = 80.0) -> tuple[str | None, float]:
    """Institution id whose id / French / English name best matches ``text`` (token-set fuzzy)."""
    if not text:
        return None, 0.0
    from rapidfuzz import fuzz

    t = _k(text)
    best, score = None, 0.0
    for i in institutions():
        s = 0.0
        if re.search(rf"\b{re.escape(i['id'].lower())}\b", t):
            s = 100.0
        for nm in (i.get("name_fr"), i.get("name_en")):
            if nm:
                s = max(s, fuzz.token_set_ratio(t, _k(nm)) if len(t) > 8 else 0.0,
                        fuzz.partial_ratio(_k(nm), t) if len(t) > 15 else 0.0,
                        fuzz.partial_ratio(_k(nm).replace(" ", ""), t.replace(" ", "")) if len(t) > 15 else 0.0)
        if s > score:
            best, score = i["id"], s
    return (best, score) if score >= min_score else (None, score)


def match_programme(text: str | None, institution_id: str | None = None, min_score: float = 85.0
                    ) -> tuple[str | None, float]:
    """Programme id (optionally restricted to an institution) from a free-text programme name."""
    if not text:
        return None, 0.0
    from rapidfuzz import fuzz

    t = _k(re.sub(r"\((?:licence|master|bachelor|bts|doctorat)[^)]*\)", "", str(text), flags=re.I))
    best, score = None, 0.0
    for i in institutions():
        if institution_id and i["id"] != institution_id:
            continue
        for p in i.get("programmes", []):
            s = 100.0 if t == _k(p["id"]) else 0.0
            for nm in (p.get("name_fr"), p.get("name_en")):
                if nm:
                    s = max(s, float(fuzz.ratio(t, _k(nm))),
                            float(fuzz.ratio(t.replace(" ", ""), _k(nm).replace(" ", ""))))
            if s > score:
                best, score = p["id"], s
    return (best, score) if score >= min_score else (None, score)


def context_line(institution_id: str | None, programme_id: str | None, year: int | None, label: str = "") -> str:
    """Human-readable context appended to chunk text so FTS finds rows by institution/programme/year."""
    parts: list[str] = []
    inst = get_institution(institution_id)
    if inst:
        parts += [inst.get("name_fr", ""), institution_id or ""]
    elif institution_id:
        parts.append(institution_id)
    prog = get_programme(programme_id, institution_id)
    if prog:
        parts += [prog.get("name_fr", ""), prog.get("name_en", ""), programme_id or ""]
    elif programme_id:
        parts.append(programme_id)
    if year:
        parts.append(str(year))
    if label:
        parts.append(label)
    return " ".join(p for p in parts if p)
