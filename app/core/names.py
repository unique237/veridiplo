"""Name normalisation shared by ingestion, retrieval and consistency.

Contract: ``normalize_name`` = unidecode, UPPERCASE, titles (M., Mme, Mlle, Dr, Pr ...) and punctuation stripped,
hyphens/apostrophes treated as separators, whitespace collapsed.  ``name_key`` sorts the tokens so that
"DAIROU Anne-Marie" and "Anne-Marie DAIROU" share the same key (surname/given-name order independent).
This is intentionally identical to ``app.kg.mapping.normalise_name`` / ``name_key``.
"""
from __future__ import annotations

import re

from unidecode import unidecode

TITLES = {"M", "MR", "MME", "MLLE", "MS", "MRS", "MISS", "DR", "PR", "PROF", "MONSIEUR", "MADAME", "MADEMOISELLE",
          "MASTER", "SIR", "MAITRE", "ME"}


def normalize_name(s: str | None) -> str:
    """Canonical comparison form of a person name ('' when empty)."""
    if not s:
        return ""
    t = unidecode(str(s)).upper()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    toks = t.split()
    while len(toks) > 1 and toks[0] in TITLES:
        toks.pop(0)
    return " ".join(toks)


def name_key(s: str | None) -> str:
    """Order-independent key: normalised tokens sorted alphabetically."""
    return " ".join(sorted(normalize_name(s).split()))


def name_tokens(s: str | None) -> list[str]:
    return normalize_name(s).split()


def name_similarity(a: str | None, b: str | None) -> float:
    """0-100 token-sort similarity between two names (rapidfuzz), order independent."""
    from rapidfuzz import fuzz

    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 0.0
    return float(fuzz.ratio(ka, kb))
