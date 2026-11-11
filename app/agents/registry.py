"""Registry spreadsheets (CSV / XLSX) -> ``registry_row`` chunks.

Handles dirty real-world files: unknown delimiter (`,` / `;` / tab), utf-8 / cp1252, title rows above the header
(xlsx), FR + EN headers, null tokens (N/A, -, empty), comma decimals, mixed date formats, mention synonyms and
sessions written as "juillet 2019" / "2017/2018".
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from unidecode import unidecode

from app.core import refdata
from app.core.names import normalize_name
from app.core.normalize import (clean_serial, clean_text, is_null, normalize_mention, parse_average, parse_date,
                                parse_session_year)

# canonical field -> header phrases (folded, all words must appear in the folded header)
_HEADERS: list[tuple[str, list[str]]] = [
    ("matricule", ["matricule", "student id", "student number", "registration number", "reg no", "reg number"]),
    ("fullname", ["nom et prenoms", "noms et prenoms", "full name", "name and surname", "candidate name"]),
    ("nom", ["nom de famille", "nom", "surname", "family name", "last name"]),
    ("prenoms", ["prenoms", "prenom", "given names", "given name", "first name", "first names", "forenames"]),
    ("birth_date", ["date de naissance", "date naissance", "date of birth", "birth date", "dob", "birthdate"]),
    ("birth_place", ["lieu de naissance", "lieu naissance", "place of birth", "birth place", "birthplace"]),
    ("programme", ["programme", "program", "filiere", "formation", "parcours", "diplome prepare"]),
    ("session", ["session", "annee", "year", "promotion", "academic year"]),
    ("average", ["moyenne generale", "moyenne", "average", "avg", "gpa", "moy"]),
    ("mention", ["mention", "grade", "classification"]),
    ("serial", ["numero diplome", "numero du diplome", "n diplome", "diploma number", "diploma no", "serial",
                "certificate number", "certificate no", "numero de serie"]),
    ("issue_date", ["date de delivrance", "date delivrance", "date of issue", "issue date", "date issued",
                    "delivered on"]),
]


def _fold(s: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unidecode(str(s)).lower()).split())


def map_header(cell: Any) -> str | None:
    f = _fold(cell)
    if not f:
        return None
    for field, phrases in _HEADERS:
        for ph in phrases:
            if f == ph:
                return field
    for field, phrases in _HEADERS:
        for ph in phrases:
            if len(ph) > 3 and re.search(rf"\b{re.escape(ph)}\b", f):
                return field
    return None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_table(path: str | Path) -> list[list[Any]]:
    """Raw cell grid (list of rows) of a CSV/XLSX file."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        import pandas as pd

        df = pd.read_excel(path, header=None, dtype=object)
        return [[None if (isinstance(v, float) and v != v) else v for v in row] for row in df.values.tolist()]
    txt = _read_text(path)
    head = "\n".join(txt.splitlines()[:5])
    try:
        delim = csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = max(",;\t|", key=head.count)
    return [row for row in csv.reader(io.StringIO(txt), delimiter=delim)]


def find_header_row(grid: list[list[Any]]) -> tuple[int, dict[int, str]] | None:
    for i, row in enumerate(grid[:15]):
        mapping = {j: m for j, c in enumerate(row) if (m := map_header(c))}
        if len(set(mapping.values())) >= 3 and ("matricule" in mapping.values() or "nom" in mapping.values()
                                                or "fullname" in mapping.values()):
            return i, mapping
    return None


def parse_registry(path: str | Path, institution_id: str | None = None, session_year: int | None = None
                   ) -> list[dict[str, Any]]:
    """Registry rows as dicts with the ``registry_row`` field contract:

    {matricule, nom, prenoms, name_raw, name_norm, birth_date, birth_place, programme, programme_raw, session,
     average, mention, serial, issue_date, source_row}
    """
    grid = read_table(path)
    hdr = find_header_row(grid)
    if hdr is None:
        return []
    hi, mapping = hdr
    out: list[dict[str, Any]] = []
    for ri, row in enumerate(grid[hi + 1:], start=hi + 2):
        rec: dict[str, Any] = {}
        for j, field in mapping.items():
            if j < len(row) and field not in rec:
                rec[field] = row[j]
        if all(is_null(v) for v in rec.values()):
            continue
        nom, pre = clean_text(rec.get("nom")), clean_text(rec.get("prenoms"))
        full = clean_text(rec.get("fullname"))
        name_raw = full or " ".join(x for x in (nom, pre) if x)
        if not name_raw:
            continue
        prog_raw = clean_text(rec.get("programme"))
        pid = None
        if prog_raw:
            pid, _ = refdata.match_programme(prog_raw, institution_id)
        sess = parse_session_year(rec.get("session")) or session_year
        out.append({
            "matricule": (re.sub(r"\s+", "", str(rec["matricule"])).upper() if not is_null(rec.get("matricule")) else None),
            "nom": nom, "prenoms": pre, "name_raw": name_raw, "name_norm": normalize_name(name_raw),
            "birth_date": parse_date(rec.get("birth_date")), "birth_place": clean_text(rec.get("birth_place")),
            "programme": pid or prog_raw, "programme_raw": prog_raw,
            "session": sess, "average": parse_average(rec.get("average")),
            "mention": normalize_mention(rec.get("mention")), "mention_raw": clean_text(rec.get("mention")),
            "serial": clean_serial(rec.get("serial")), "issue_date": parse_date(rec.get("issue_date")),
            "source_row": ri,
        })
    return out


def registry_row_text(r: dict[str, Any]) -> str:
    avg = f"{r['average']:.2f}" if r.get("average") is not None else "-"
    return " | ".join([
        f"Registre ligne {r.get('source_row')}", r.get("matricule") or "-", r["name_raw"],
        r.get("programme") or "-", str(r.get("session") or "-"), avg, r.get("mention") or "-",
        r.get("serial") or "-", r.get("issue_date") or "-"])


def build_registry_chunks(path: str | Path, institution_id: str | None, session_year: int | None
                          ) -> list[dict[str, Any]]:
    chunks = []
    for i, r in enumerate(parse_registry(path, institution_id, session_year), start=1):
        year = r["session"] or session_year
        pid = refdata.get_programme(r["programme"], institution_id) and r["programme"]
        ctx = refdata.context_line(institution_id, pid, year, "registre des diplômés")
        chunks.append({
            "kind": "registry_row", "page": 1, "row_idx": i,
            "text": f"{registry_row_text(r)} | {ctx}", "fields": r, "bbox": None, "page_size": None,
            "ocr_conf": None, "programme_id": pid, "session_year": year, "name_norm": r["name_norm"][:256],
        })
    return chunks
