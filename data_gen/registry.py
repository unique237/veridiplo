"""Registry (CSV / XLSX) writers with realistic mess: nulls, mixed formats, swapped columns."""
from __future__ import annotations

import csv
import io
import os
import random
import re
import zipfile
from datetime import datetime

from .names import strip_accents
from .render import MONTHS_EN, MONTHS_FR, WATERMARK

COLUMNS = ["matricule", "nom", "prenoms", "date_naissance", "lieu_naissance", "programme", "session",
           "moyenne", "mention", "numero_diplome", "date_delivrance", "avertissement"]
NULLS = ["", "", "", "N/A", "-", "NULL", "n/d"]


def _fmt_date(iso: str, style: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    if style == "iso":
        return iso
    if style == "dmy_slash":
        return f"{d:02d}/{m:02d}/{y}"
    if style == "dmy_dash":
        return f"{d:02d}-{m:02d}-{y}"
    if style == "dmy_dot":
        return f"{d:02d}.{m:02d}.{y}"
    if style == "long_fr":
        return f"{d} {MONTHS_FR[m - 1]} {y}"
    if style == "long_en":
        return f"{d} {MONTHS_EN[m - 1][:3]} {y}"
    raise ValueError(style)


ALL_STYLES = ["iso", "dmy_slash", "dmy_dash", "dmy_dot", "long_fr", "long_en"]


def build_rows(grads: list[dict], prog_by_id: dict, style: str, decimal_comma: bool, rng: random.Random,
               protected: set[str], serial_override: dict[str, str]) -> list[dict]:
    """grads: graduate records of one institution/year.  protected: person_ids whose key fields stay clean."""
    rows = []
    for g in grads:
        prot = g["person_id"] in protected
        prog = prog_by_id[g["programme_id"]]
        nom, prenoms = g["family_name"], g["given_names"]
        dob = _fmt_date(g["birth_date"], style if (prot or rng.random() < 0.85) else rng.choice(ALL_STYLES))
        place = g["birth_place"]
        issue = _fmt_date(g["issue_date"], style if (prot or rng.random() < 0.85) else rng.choice(ALL_STYLES))
        avg = f"{g['average']:.2f}"
        mention = g["mention"]
        serial = serial_override.get(g["person_id"], g["diploma_serial"])
        sess = str(g["session_year"])
        progcol = prog["name_fr"] if g["language"] == "fr" else prog["name_en"]
        # --- formatting dirt (safe for protected rows)
        if decimal_comma or rng.random() < 0.12:
            avg = avg.replace(".", ",")
        r = rng.random()
        if r < 0.08:
            avg = avg.rstrip("0").rstrip(".,") if "." in avg or "," in avg else avg
        elif r < 0.10:
            avg = avg + "/20"
        if rng.random() < 0.10:
            mention = mention.upper() if rng.random() < 0.5 else mention.lower()
        if rng.random() < 0.06:
            nom = " " + nom + " "
        if rng.random() < 0.05:
            serial = serial + " "
        if rng.random() < 0.25:
            progcol = g["programme_id"]
        r = rng.random()
        if r < 0.15:
            sess = f"{g['session_year'] - 1}/{g['session_year']}"
        elif r < 0.22:
            sess = f"{(MONTHS_EN if g['language'] == 'en' else MONTHS_FR)[int(g['deliberation_date'][5:7]) - 1]} {g['session_year']}"
        # --- content dirt (never on protected rows)
        if not prot:
            if rng.random() < 0.02:
                nom, prenoms = prenoms, nom
            if rng.random() < 0.05:
                nom = nom.title()
            if rng.random() < 0.15:
                place = strip_accents(place)
            if rng.random() < 0.03:
                place = place[: max(3, len(place) - 2)]
            if rng.random() < 0.05:
                prenoms = strip_accents(prenoms)
            for fld in ("dob", "place", "avg", "mention", "serial", "issue"):
                p = {"dob": 0.03, "place": 0.04, "avg": 0.025, "mention": 0.025, "serial": 0.06, "issue": 0.08}[fld]
                if rng.random() < p:
                    v = rng.choice(NULLS)
                    if fld == "dob":
                        dob = v
                    elif fld == "place":
                        place = v
                    elif fld == "avg":
                        avg = v
                    elif fld == "mention":
                        mention = v
                    elif fld == "serial":
                        serial = v
                    else:
                        issue = v
        rows.append({
            "matricule": g["matricule"], "nom": nom, "prenoms": prenoms, "date_naissance": dob,
            "lieu_naissance": place, "programme": progcol, "session": sess, "moyenne": avg,
            "mention": mention, "numero_diplome": serial, "date_delivrance": issue,
            "avertissement": WATERMARK,
        })
    return rows


def write_csv(rows: list[dict], path: str, delimiter: str = ",") -> None:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=delimiter, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())


def write_xlsx(rows: list[dict], path: str, title: str) -> None:
    """Spreadsheet with a title block above the header, a blank row and a totals footer."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Registre"
    ws["A1"] = title
    ws["A2"] = WATERMARK
    ws.append([])
    ws.append(COLUMNS)
    for r in rows:
        ws.append([r[c] if r[c] != "" else None for c in COLUMNS])
    ws.append([])
    ws.append([f"Total diplômés : {len(rows)}"])
    ws.column_dimensions["C"].width = 28
    fixed = datetime(2020, 1, 1)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.properties.creator = "VeriDiplo synthetic data generator"
    wb.properties.lastModifiedBy = "VeriDiplo synthetic data generator"
    tmp = path + ".tmp"
    wb.save(tmp)
    # rewrite the zip with fixed timestamps for byte-level determinism
    with zipfile.ZipFile(tmp) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in sorted(zin.namelist()):
            zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            data = zin.read(name)
            if name == "docProps/core.xml":  # openpyxl stamps 'modified' with the current time on save
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2020-01-01T00:00:00Z", data)
            zout.writestr(zi, data)
    os.remove(tmp)
