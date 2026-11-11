"""Value normalisers used by ingestion/extraction: averages, mentions, dates, sessions, serials, decisions."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from unidecode import unidecode

from app.core.names import name_key, normalize_name  # noqa: F401  (re-export)

NULL_TOKENS = {"", "N/A", "NA", "NAN", "NONE", "NULL", "-", "--", "—", "?", "NC", "N.C.", "ND", "NON DISPONIBLE", "INCONNU"}

MENTIONS = ["Passable", "Assez Bien", "Bien", "Très Bien"]  # canonical French labels (ascending)
_MENTION_ALIASES = {
    "PASSABLE": "Passable", "PASS": "Passable", "P": "Passable", "PASSABLE E": "Passable",
    "ASSEZ BIEN": "Assez Bien", "AB": "Assez Bien", "A BIEN": "Assez Bien", "ASS BIEN": "Assez Bien",
    "FAIRLY GOOD": "Assez Bien", "SECOND CLASS UPPER": "Assez Bien", "LOWER SECOND": "Assez Bien",
    "BIEN": "Bien", "B": "Bien", "GOOD": "Bien",
    "TRES BIEN": "Très Bien", "TB": "Très Bien", "T BIEN": "Très Bien", "VERY GOOD": "Très Bien",
    "EXCELLENT": "Très Bien", "DISTINCTION": "Très Bien",
}


def is_null(v: Any) -> bool:
    if v is None:
        return True
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return True
    except Exception:  # pragma: no cover
        pass
    return str(v).strip().upper() in NULL_TOKENS


def parse_average(v: Any) -> float | None:
    """'13,45' / '13.45' / ' 13.4 /20' / 13.45 -> float; nulls and out-of-range (not in [0,20]) -> None."""
    if is_null(v):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).strip().replace(" ", "").replace(" ", "")
        s = re.sub(r"/20$", "", s)
        s = s.replace(",", ".")
        m = re.search(r"\d+(?:\.\d+)?", s)
        if not m:
            return None
        f = float(m.group(0))
    if f < 0 or f > 20:
        return None
    return round(f, 2)


def _mention_key(v: str) -> str:
    return " ".join(re.sub(r"[^A-Z]+", " ", unidecode(v).upper()).split())


def normalize_mention(v: Any, fuzzy: bool = True) -> str | None:
    """Mention label (FR/EN, any case/accents/abbreviation, OCR noise) -> canonical French label or None."""
    if is_null(v):
        return None
    k = _mention_key(str(v))
    if not k:
        return None
    if k in _MENTION_ALIASES:
        return _MENTION_ALIASES[k]
    if fuzzy:
        from rapidfuzz import fuzz, process

        cands = [_mention_key(m) for m in MENTIONS] + [a for a in _MENTION_ALIASES if len(a) > 3]
        best = process.extractOne(k, cands, scorer=fuzz.ratio, score_cutoff=80)
        if best:
            return _MENTION_ALIASES.get(best[0]) or {_mention_key(m): m for m in MENTIONS}[best[0]]
    return None


def mention_for_average(avg: float | None) -> str | None:
    """Policy bands (CLAUDE.md data_gen): Passable 10-12, Assez Bien 12-14, Bien 14-16, Très Bien >=16."""
    if avg is None:
        return None
    if avg >= 16:
        return "Très Bien"
    if avg >= 14:
        return "Bien"
    if avg >= 12:
        return "Assez Bien"
    if avg >= 10:
        return "Passable"
    return None


def normalize_decision(v: Any) -> str | None:
    if is_null(v):
        return None
    k = _mention_key(str(v))
    if k.startswith("ADMI") or k in ("A", "PASSED"):
        return "Admis"
    if k.startswith(("AJOURN", "FAIL", "REFUS", "ECHEC", "REJECT")):
        return "Ajourné"
    from rapidfuzz import fuzz, process

    best = process.extractOne(k, {"ADMIS": "Admis", "ADMITTED": "Admis", "AJOURNE": "Ajourné", "FAILED": "Ajourné"},
                              scorer=fuzz.ratio, score_cutoff=70)
    return best[0] if best else None


_FR_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
              "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
_EN_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
              "september": 9, "october": 10, "november": 11, "december": 12}
_MONTHS = {**_FR_MONTHS, **_EN_MONTHS}


def _mk(y: int, m: int, d: int) -> str | None:
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(v: Any) -> str | None:
    """Any common FR/EN date notation -> ISO-8601 (YYYY-MM-DD); None when no full date is recognisable."""
    if is_null(v):
        return None
    if isinstance(v, (dt.datetime,)):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "to_pydatetime"):
        try:
            return v.to_pydatetime().date().isoformat()
        except Exception:
            return None
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ].*)?", s)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})", s)
    if m:
        return _mk(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    k = unidecode(s).lower()
    k = re.sub(r"\b[li|]er\b", "1er", k)  # OCR: "1er" read as "ler"
    k = re.sub(r"[.;:]", " ", k)
    m = re.search(r"(\d{1,2})(?:er|st|nd|rd|th)?\s*(?:of\s+)?([a-z]+)\.?,?\s*(\d{4})", k)
    if m and m.group(2) in _MONTHS:
        return _mk(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"([a-z]+)\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})", k)
    if m and m.group(1) in _MONTHS:
        return _mk(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    return None


def parse_session_year(v: Any) -> int | None:
    """'2019', 'juillet 2019', '2017/2018' (academic year -> the closing year 2018), 2019.0 -> int."""
    if is_null(v):
        return None
    if isinstance(v, (int, float)):
        y = int(v)
        return y if 1900 <= y <= 2100 else None
    years = [int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", str(v))]
    return max(years) if years else None


def clean_serial(v: Any) -> str | None:
    """Diploma serial: nulls -> None, whitespace removed, upper-cased."""
    if is_null(v):
        return None
    s = re.sub(r"\s+", "", str(v)).upper()
    return s or None


def clean_text(v: Any) -> str | None:
    if is_null(v):
        return None
    return " ".join(str(v).split())
