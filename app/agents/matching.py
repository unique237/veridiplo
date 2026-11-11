"""Pure name/serial matching helpers shared by retrieval and consistency (no database, no I/O).

``name_sim`` is rapidfuzz ``token_sort_ratio`` on normalised names (accent, case and name-order insensitive).
If rapidfuzz is not installed an exact pure-python equivalent (normalised Indel similarity) is used, so the
consistency checks stay importable and testable anywhere.
"""
from __future__ import annotations

from typing import Any, Optional

from app.core.names import normalize_name

SERIAL_KEYS = ("serial", "serial_number", "numero_diplome", "diploma_number", "numero")

try:
    from rapidfuzz.fuzz import ratio as _ratio
    from rapidfuzz.fuzz import token_sort_ratio as _tsr
except Exception:  # pragma: no cover - exercised only without rapidfuzz
    _tsr = _ratio = None


def _lcs_len(a: str, b: str) -> int:
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def _token_sort_ratio(a: str, b: str) -> float:
    sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    if not sa and not sb:
        return 100.0
    return 100.0 * 2 * _lcs_len(sa, sb) / (len(sa) + len(sb))


def _plain_ratio(a: str, b: str) -> float:
    if _ratio is not None:
        return float(_ratio(a, b))
    return 100.0 * 2 * _lcs_len(a, b) / (len(a) + len(b)) if (a or b) else 100.0


def token_align_sim(a: str, b: str) -> float:
    """0-100 token-alignment similarity: every token is compared with its best counterpart, weighted by length.

    Robust to a single OCR error in a token that would change the token-sort order (e.g. a wrong first letter).
    It is only used to avoid turning an OCR slip into a ``fail``; it can never upgrade a name to ``pass``.
    """
    ta, tb = normalize_name(a).split(), normalize_name(b).split()
    if not ta or not tb:
        return 0.0

    def one_way(x: list[str], y: list[str]) -> float:
        tot = sum(len(t) for t in x)
        return sum(len(t) * max(_plain_ratio(t, u) for u in y) for t in x) / tot

    return (one_way(ta, tb) + one_way(tb, ta)) / 2


def name_sim(a: str, b: str) -> float:
    """0-100 token-sort similarity of two names (both normalised here, so any order/accents work)."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return float((_tsr or _token_sort_ratio)(na, nb))


def row_name(fields: dict[str, Any], name_norm: Optional[str] = None) -> str:
    """Best available holder name of a PV / registry row, normalised."""
    for k in ("name_norm", "name_raw", "name", "holder_name"):
        v = fields.get(k)
        if v:
            return normalize_name(v)
    if fields.get("nom") or fields.get("prenoms"):
        return normalize_name(f"{fields.get('nom') or ''} {fields.get('prenoms') or ''}")
    return normalize_name(name_norm or "")


def row_serial(fields: dict[str, Any]) -> Optional[str]:
    for k in SERIAL_KEYS:
        v = fields.get(k)
        if v not in (None, ""):
            s = "".join(str(v).split()).upper()
            if s and s not in ("-", "NAN", "NONE", "NULL"):
                return s
    return None
