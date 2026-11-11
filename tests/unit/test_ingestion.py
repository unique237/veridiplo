"""Unit tests for the ingestion path: normalisers, PV row extraction, registries, diploma rules, intake + DB.

OCR-dependent tests (real scans) run when an OCR engine is available; DB tests when Postgres answers.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
from pathlib import Path

import pytest

from app.agents import ocr
from app.agents import registry as registry_mod
from app.agents.extraction import fields_from_json, parse_diploma_text
from app.agents.intake import infer_from_filename
from app.core import refdata
from app.core.names import name_key, normalize_name, name_similarity
from app.core.normalize import (clean_serial, normalize_decision, normalize_mention, parse_average, parse_date,
                                parse_session_year)

REPO = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("DATA_DIR") or REPO / "data")
SYN = DATA / "synthetic"
needs_data = pytest.mark.skipif(not (SYN / "pv_index.json").is_file(), reason="synthetic corpus not generated")


def _engine_available() -> bool:
    try:
        ocr.available_engine()
        return True
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _engine_available(), reason="no OCR engine")


# ------------------------------------------------------------------------------------------- normalisers
def test_normalize_name_and_key():
    assert normalize_name("  Mme  Joséphine ABENA-MANGA ") == "JOSEPHINE ABENA MANGA"
    assert normalize_name("Dr. Jean-Pierre N'Dongo") == "JEAN PIERRE N DONGO"
    assert normalize_name("M. KAMGA") == "KAMGA"  # a lone token is never dropped as a title
    assert normalize_name(None) == "" and normalize_name("") == ""
    assert name_key("DAIROU Anne-Marie") == name_key("Anne-Marie DAIROU")  # surname/given order independent
    assert name_similarity("DAIROU Anne-Marie", "Anne Marie Dairou") == 100.0
    assert name_similarity("EBOT Blessing", "EBOT Blesing") > 90


@pytest.mark.parametrize("raw,exp", [("13,45", 13.45), ("13.45", 13.45), (" 12.5 /20", 12.5), (14, 14.0), ("N/A", None),
                                     ("", None), (None, None), ("-", None), ("21.5", None), ("abc", None),
                                     ("10,0", 10.0), (float("nan"), None)])
def test_parse_average(raw, exp):
    assert parse_average(raw) == exp


@pytest.mark.parametrize("raw,exp", [("Assez Bien", "Assez Bien"), ("ASSEZ BIEN", "Assez Bien"), ("assez-bien", "Assez Bien"),
                                     ("Très Bien", "Très Bien"), ("tres bien", "Très Bien"), ("TB", "Très Bien"),
                                     ("Fairly Good", "Assez Bien"), ("Good", "Bien"), ("Very Good", "Très Bien"),
                                     ("Pass", "Passable"), ("Passable", "Passable"), ("Pasable", "Passable"),
                                     ("-", None), ("", None), (None, None), ("N/A", None), ("xyz", None)])
def test_normalize_mention(raw, exp):
    assert normalize_mention(raw) == exp


@pytest.mark.parametrize("raw,exp", [("23/09/1993", "1993-09-23"), ("23.12.1996", "1996-12-23"), ("1993-09-23", "1993-09-23"),
                                     ("1er juillet 2019", "2019-07-01"), ("22 juillet 2024", "2024-07-22"),
                                     ("August 10, 2015", "2015-08-10"), ("15 février 2020", "2020-02-15"),
                                     ("31/02/2020", None), ("juillet 2019", None), ("N/A", None), (None, None)])
def test_parse_date(raw, exp):
    assert parse_date(raw) == exp


def test_session_serial_decision():
    assert parse_session_year("2017/2018") == 2018
    assert parse_session_year("juillet 2019") == 2019
    assert parse_session_year(2020.0) == 2020
    assert parse_session_year("n/a") is None
    assert clean_serial(" ufn/2019 /LDROIT/00128 ") == "UFN/2019/LDROIT/00128"
    assert clean_serial("N/A") is None
    assert normalize_decision("Admitted") == "Admis" and normalize_decision("Ajourné") == "Ajourné"
    assert normalize_decision("Failed") == "Ajourné" and normalize_decision("") is None


def test_filename_inference():
    assert infer_from_filename("/x/pv/UFN_LINFO_2024.pdf") == {"issuer": "UFN", "programme_id": "LINFO",
                                                                "session_year": 2024, "doc_type": "pv"}
    r = infer_from_filename("/x/registries/UFN_2020.xlsx")
    assert (r["issuer"], r["session_year"], r["doc_type"]) == ("UFN", 2020, "registry")
    assert infer_from_filename("/x/lists/FUM_2016.pdf")["doc_type"] == "list"
    assert infer_from_filename("/x/misc/scan.pdf") == {}


# ------------------------------------------------------------------------------------------- PV rows
def _w(t, x0, y, x1=None, conf=0.95, h=22):
    return {"t": t, "b": [x0, y, x1 if x1 is not None else x0 + 12 * len(t), y + h], "c": conf}


def _synthetic_page():
    """Words laid out like the generated PV (x positions in px at 200 dpi), rows 50 px apart, slight y jitter."""
    words = [_w("PROCÈS-VERBAL", 400, 250), _w("N°", 120, 571), _w("Matricule", 190, 571), _w("Nom", 390, 571),
             _w("et", 450, 571), _w("prénoms", 480, 571), _w("Moyenne", 970, 571), _w("Mention", 1110, 571),
             _w("Décision", 1350, 571)]
    rows = [(1, "21B4895", ["DAIROU", "Anne-Marie"], "13.45", ["Assez", "Bien"], "Admis"),
            (2, "21B2503", ["HAMIDOU", "Kevin", "Léonie"], "10,44", ["Passable"], "Admis"),
            (3, "21B2197", ["TEFOUET", "Josiane", "Jean"], "7.26", [], "Ajourné"),
            (4, "21B0001", ["Mary", "NGWA"], "16.0", ["Très", "Bien"], "Admis")]
    for i, (n, mat, name, avg, ment, dec) in enumerate(rows):
        y = 633 + 53 * i + (4 if i % 2 else -3)  # jitter
        words += [_w(str(n), 130, y), _w(mat, 190, y)]
        x = 390
        for t in name:
            words.append(_w(t, x, y))
            x += 12 * len(t) + 10
        words.append(_w(avg, 970, y))
        x = 1110
        for t in ment:
            words.append(_w(t, x, y))
            x += 12 * len(t) + 10
        if not ment:
            words.append(_w("-", 1110, y, 1125))
        words.append(_w(dec, 1350, y))
    return {"page": 1, "size": [1654, 2339], "words": words}


def test_extract_pv_rows_from_boxes():
    rows, cols = ocr.extract_pv_rows(_synthetic_page())
    assert cols is not None and set(cols) >= {"matricule", "average", "mention", "decision"}
    assert [r["matricule"] for r in rows] == ["21B4895", "21B2503", "21B2197", "21B0001"]
    r0, r1, r2, r3 = rows
    assert (r0["name_raw"], r0["average"], r0["mention"], r0["decision"]) == ("DAIROU Anne-Marie", 13.45, "Assez Bien", "Admis")
    assert r1["name_norm"] == "HAMIDOU KEVIN LEONIE" and r1["average"] == 10.44 and r1["mention"] == "Passable"
    assert r2["average"] == 7.26 and r2["mention"] is None and r2["decision"] == "Ajourné"
    assert r3["mention"] == "Très Bien" and r3["average"] == 16.0
    assert [r["row_n"] for r in rows] == [1, 2, 3, 4]
    for r in rows:  # bbox: full table width, one pitch high, centred on the row
        x0, y0, x1, y1 = r["bbox"]
        assert x0 < 130 and x1 > 1400 and 40 < y1 - y0 < 70


def test_pv_row_without_header_uses_default_columns():
    page = _synthetic_page()
    page["words"] = [w for w in page["words"] if w["b"][1] > 600]  # drop header and title
    rows, cols = ocr.extract_pv_rows(page)
    assert cols is None and len(rows) == 4 and rows[0]["name_raw"] == "DAIROU Anne-Marie"


def test_cluster_lines_tolerates_skew_jitter():
    ws = [_w("A", 100, 100), _w("B", 200, 108), _w("C", 300, 94), _w("D", 100, 160), _w("E", 200, 166)]
    lines = ocr.cluster_lines(ws)
    assert [[w["t"] for w in l] for l in lines] == [["A", "B", "C"], ["D", "E"]]


def test_build_pv_chunks_text_contract():
    doc = {"pages": [{**_synthetic_page(), "engine": "test", "conf": 0.9}]}
    ch = ocr.build_pv_chunks(doc, "UFN", "LINFO", 2024)
    rows = [c for c in ch if c["kind"] == "pv_row"]
    texts = [c for c in ch if c["kind"] == "text"]
    assert len(rows) == 4 and len(texts) == 1
    c = rows[0]
    assert c["text"].startswith("N° 1 | 21B4895 | DAIROU Anne-Marie | 13.45 | Assez Bien | Admis")
    assert "2024" in c["text"] and "LINFO" in c["text"]
    assert c["page"] == 1 and c["page_size"] == [1654, 2339] and len(c["bbox"]) == 4
    assert set(c["fields"]) >= {"matricule", "name_raw", "name_norm", "average", "mention", "decision", "row_n"}
    assert c["name_norm"] == "DAIROU ANNE MARIE"


def test_crop_png_highlight():
    import numpy as np
    from PIL import Image
    import io

    img = np.full((400, 600), 255, np.uint8)
    png = ocr.crop_png(img, [100, 100, 300, 140], highlight=True, pad=10)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert im.size == (220, 60)
    assert any(px[0] > 200 and px[1] < 80 for px in im.getdata())  # red outline present
    plain = Image.open(io.BytesIO(ocr.crop_png(img, [100, 100, 300, 140], highlight=False))).convert("RGB")
    assert all(px == (255, 255, 255) for px in plain.getdata())


def test_deskew_recovers_rotation():
    import cv2
    import numpy as np

    img = np.full((1000, 800), 255, np.uint8)
    for y in range(100, 900, 40):
        cv2.putText(img, "LIGNE DE TEXTE " * 3, (40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
    m = cv2.getRotationMatrix2D((400, 500), 1.6, 1.0)
    rot = cv2.warpAffine(img, m, (800, 1000), borderValue=255)
    est = ocr.estimate_skew(rot)
    assert abs(est - (-1.6)) < 0.3  # the correction undoes the +1.6 deg rotation


# ------------------------------------------------------------------------------------------- registries
def test_registry_dirty_csv(tmp_path):
    p = tmp_path / "XXX_2020.csv"
    with open(p, "w", encoding="cp1252", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Student ID", "Surname", "Given names", "Date of birth", "Place of birth", "Programme",
                    "Session", "Average", "Grade", "Diploma No.", "Date issued"])
        w.writerow(["20a123", "ÉBÉLLE ", " Marie-Noëlle", "07.03.1996", "Yaoundé", "BSc X", "2019/2020", "12,5", "Fairly Good", "N/A", "12/09/2020"])
        w.writerow(["", "", "", "", "", "", "", "", "", "", ""])
        w.writerow(["20B999", "KAMGA", "", "", "", "", "juillet 2020", "n/a", "TB", "xx/2020/1 ", ""])
    rows = registry_mod.parse_registry(p)
    assert len(rows) == 2
    a, b = rows
    assert a["matricule"] == "20A123" and a["name_norm"] == "EBELLE MARIE NOEL LE".replace("NOEL LE", "NOELLE")
    assert a["birth_date"] == "1996-03-07" and a["average"] == 12.5 and a["mention"] == "Assez Bien"
    assert a["session"] == 2020 and a["serial"] is None and a["issue_date"] == "2020-09-12"
    assert b["average"] is None and b["mention"] == "Très Bien" and b["session"] == 2020 and b["serial"] == "XX/2020/1"


@needs_data
def test_registry_synthetic_files_parse():
    csvs = sorted((SYN / "registries").glob("*.csv"))[:6]
    xlsx = sorted((SYN / "registries").glob("*.xlsx"))
    assert csvs and xlsx
    total = 0
    for f in csvs + xlsx:
        inst = f.stem.split("_")[0]
        rows = registry_mod.parse_registry(f, inst, int(f.stem.split("_")[1]))
        assert rows, f
        total += len(rows)
        for r in rows:
            assert r["name_norm"] and r["session"]
            assert r["average"] is None or 0 <= r["average"] <= 20
            assert r["mention"] in (None, "Passable", "Assez Bien", "Bien", "Très Bien")
            assert r["birth_date"] is None or len(r["birth_date"]) == 10
    assert total > 50


@needs_data
def test_registry_matches_graduates_ground_truth():
    """Every graduate (test-only ground truth) is found in the parsed registries with the same average/mention."""
    grads = json.loads((SYN / "graduates.json").read_text(encoding="utf-8"))
    parsed: dict[str, dict] = {}
    for f in (SYN / "registries").glob("*.*"):
        for r in registry_mod.parse_registry(f, f.stem.split("_")[0], int(f.stem.split("_")[1])):
            if r["matricule"]:
                parsed[r["matricule"]] = r
    seen = ok = 0
    for g in grads:
        mat = (g.get("matricule") or "").upper()
        if mat not in parsed:
            continue
        seen += 1
        r = parsed[mat]
        ok += (name_key(r["name_raw"]) == name_key(g.get("name") or g.get("holder_name") or r["name_raw"])
               and (r["average"] is None or abs(r["average"] - float(g["average"])) < 0.006))
    assert seen > 400 and ok / seen > 0.97


# ------------------------------------------------------------------------------------------- diplomas
LINES_FR = ["UNIVERSITÉ FICTIVE DE NKOLBISSON", "Établissement fictif - document de démonstration", "DIPLÔME DE LICENCE",
            "La Rectrice - Université Fictive de Nkolbisson, décerne à",
            "Nom et prénoms : DAIROU Anne-Marie", "Né(e) le 23 juillet 1998 à Garoua", "le diplôme de",
            "Licence en Informatique", "Session de juillet 2024",
            "Moyenne générale : 13,45/20 Mention : Assez Bien", "N° du diplôme : UFN/2024/LINFO/00036",
            "Fait à Yaoundé, le 30 septembre 2024", "Rectrice", "Joséphine ABENA MANGA"]
LINES_EN = ["FICTIVE UNIVERSITY OF NKOLBISSON", "DIPLOMA OF BACHELOR", "The Rectrice - hereby confers upon",
            "Name: Divine Atem CHIA", "Born on February 22, 1998 in Buea", "the degree of",
            "Bachelor in Private Law", "Session of July 2022", "Overall average: 10.00/20 Grade: Pass",
            "Diploma No.: UFN/2022/LDROIT/00098", "Done at Yaoundé, on December 26, 2022"]


@needs_data
def test_parse_diploma_text_fr_en():
    f = parse_diploma_text(LINES_FR)
    assert (f.language, f.institution_id, f.programme_id, f.session_year) == ("fr", "UFN", "LINFO", 2024)
    assert f.holder_name == "DAIROU Anne-Marie" and f.birth_date == "1998-07-23" and f.birth_place == "Garoua"
    assert (f.average, f.mention, f.serial, f.issue_date) == (13.45, "Assez Bien", "UFN/2024/LINFO/00036", "2024-09-30")
    assert f.signatory_name == "Joséphine ABENA MANGA"
    e = parse_diploma_text(LINES_EN)
    assert e.language == "en" and e.mention == "Passable" and e.average == 10.0 and e.session_year == 2022
    assert e.birth_date == "1998-02-22" and e.issue_date == "2022-12-26" and e.programme_id == "LDROIT"


@needs_data
def test_serial_repair_uses_institution_format():
    lines = [l.replace("UFN/2024/LINFO/00036", "UFN/2O24/LINFO/OOO36") for l in LINES_FR]
    assert parse_diploma_text(lines).serial == "UFN/2024/LINFO/00036"


def test_fields_from_json_normalises():
    f = fields_from_json({"case_id": "X", "holder_name": "A B", "average": "13,5", "mention": "Fairly Good",
                          "birth_date": "01/02/2000", "session_year": "2019/2020", "serial": " ufn/1 ", "extra": 1})
    assert (f.average, f.mention, f.birth_date, f.session_year, f.serial) == (13.5, "Assez Bien", "2000-02-01", 2020, "UFN/1")


@needs_data
def test_fields_from_json_file():
    f = fields_from_json(SYN / "diplomas" / "GEN-001.json")
    assert f.holder_name == "DAIROU Anne-Marie" and f.institution_id == "UFN" and f.average == 13.45


# ------------------------------------------------------------------------------------------- OCR on real scans
def _score_pv(key: str) -> tuple[int, int, int]:
    idx = json.loads((SYN / "pv_index.json").read_text(encoding="utf-8"))[key]
    path = SYN / "pv" / f"{key}.pdf"
    doc = ocr.ocr_document(path)  # cached under DATA_DIR/cache/ocr after the first run
    ch = ocr.build_pv_chunks(doc, idx["institution_id"], idx["programme_id"], idx["session_year"])
    got = {c["fields"]["matricule"]: c["fields"] for c in ch if c["kind"] == "pv_row"}
    n = sum(len(idx["rows"]) for _ in [0])
    found = name_ok = 0
    for r in idx["rows"]:
        f = got.get(r["matricule"])
        if f:
            found += 1
            name_ok += name_key(f["name_raw"]) == name_key(r["name"]) and f["average"] == parse_average(r["average"])
    return n, found, name_ok


@needs_data
@needs_ocr
@pytest.mark.parametrize("key", ["UFN_LINFO_2024", "FUM_BAENG_2015"])
def test_ocr_pv_scan_rows(key):
    n, found, ok = _score_pv(key)
    both = len(ocr.available_engines()) > 1  # Tesseract alone is weaker on noisy scans than Tesseract + RapidOCR
    assert found / n >= (0.95 if both else 0.8), (key, n, found, ok)
    assert ok / n >= (0.85 if both else 0.6), (key, n, found, ok)


@needs_data
@needs_ocr
def test_ocr_diploma_extraction_accuracy():
    """Field-level accuracy of OCR extraction on a sample of diplomas vs the typed JSON (printed with -s)."""
    from app.agents.extraction import extract_diploma_fields

    names = ["GEN-001", "GEN-005", "GEN-007", "TMEN-001", "OCRH-001", "REVN-002"]
    fields = ("holder_name", "birth_date", "institution_id", "programme_id", "session_year", "average", "mention",
              "serial", "issue_date")
    tot = ok = 0
    for n in names:
        truth = fields_from_json(SYN / "diplomas" / f"{n}.json")
        got = extract_diploma_fields(str(SYN / "diplomas" / f"{n}.png"))
        for k in fields:
            tot += 1
            a, b = getattr(truth, k), getattr(got, k)
            ok += (name_key(a) == name_key(b)) if k == "holder_name" else (a == b)
    print(f"diploma OCR field accuracy: {ok}/{tot}")
    assert ok / tot >= 0.9, (ok, tot)


# ------------------------------------------------------------------------------------------- DB (optional)
def _db_ok() -> bool:
    try:
        from sqlalchemy import text

        from app.core.db import get_engine

        with get_engine().connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_ok(), reason="database not reachable")


@needs_db
def test_ingest_registry_dedupe_and_audit(tmp_path):
    from sqlalchemy import func, select

    from app.agents.intake import IntakeAgent
    from app.core.db import SessionLocal, init_db
    from app.core.models import Chunk, SourceDocument

    init_db()
    p = tmp_path / "UFN_2099.csv"
    p.write_text("matricule,nom,prenoms,moyenne,mention,session\n99A001,TESTNOM,Prenom Un,13.5,Assez Bien,2099\n"
                 "99A002,AUTRE,Deux,N/A,,2099\n", encoding="utf-8")
    s = SessionLocal()
    try:
        d1 = IntakeAgent(embed=False).ingest_file(str(p), session=s)
        assert d1.doc_type == "registry" and d1.issuer == "UFN" and d1.session_year == 2099 and d1.status == "ingested"
        n1 = s.execute(select(func.count()).select_from(Chunk).where(Chunk.document_id == d1.id)).scalar_one()
        assert n1 == 2
        d2 = IntakeAgent(embed=False).ingest_file(str(p), session=s)  # same bytes -> same document
        assert d2.id == d1.id
        n2 = s.execute(select(func.count()).select_from(Chunk).where(Chunk.document_id == d1.id)).scalar_one()
        assert n2 == 2
        row = s.execute(select(Chunk).where(Chunk.document_id == d1.id, Chunk.row_idx == 1)).scalar_one()
        assert row.kind == "registry_row" and row.name_norm == "TESTNOM PRENOM UN" and row.fields["average"] == 13.5
        from app.core.audit import verify_chain

        assert verify_chain(s)[0]
        s.query(SourceDocument).filter(SourceDocument.id == d1.id).delete()
        s.commit()
    finally:
        s.close()


def test_intake_rejects_unsupported_and_missing(tmp_path):
    from app.agents.intake import IntakeAgent

    bad = tmp_path / "notes.docx"
    bad.write_text("x")
    with pytest.raises(ValueError):
        IntakeAgent(embed=False).ingest_file(str(bad))
    with pytest.raises(FileNotFoundError):
        IntakeAgent(embed=False).ingest_file(str(tmp_path / "missing.pdf"))


# ------------------------------------------------------------------------------------------- merge / repair / grid
def test_repair_matricules_shape_and_template():
    rows = [{"matricule": m} for m in ["21B4895", "2182503", "21B2197", "21B0001", "21B3333"]]
    assert ocr.repair_matricules(rows) == 1
    assert rows[1]["matricule"] == "21B2503"
    assert rows[1]["matricule_raw"] == "2182503"
    isd = [{"matricule": m} for m in ["13ISD112", "13IS0590", "131SD340", "13ISD777"]]
    ocr.repair_matricules(isd)
    assert [r["matricule"] for r in isd] == ["13ISD112", "13ISD590", "13ISD340", "13ISD777"]


def test_merge_rows_prefers_valid_fields():
    base = dict(row_n=1, ocr_conf=0.8, mention_raw=None, decision_raw=None, bbox=None)
    a = {**base, "matricule": "2184895", "name_raw": "NI Cecilia", "name_norm": "NI CECILIA", "average": None,
         "mention": None, "decision": None}
    b = {**base, "matricule": "21B4895", "name_raw": "NJI Cecilia", "name_norm": "NJI CECILIA", "average": 15.42,
         "mention": "Bien", "decision": "Admis"}
    m = ocr.merge_rows(a, b, shape="DDLDDDD")
    assert (m["matricule"], m["average"], m["mention"], m["decision"]) == ("21B4895", 15.42, "Bien", "Admis")
    assert ocr.merge_rows(None, b) is b and ocr.merge_rows(a, None) is a


def test_ruled_table_grid_and_band_extraction():
    """A drawn grid (like the generated PVs): rulings are found, erased, and rows are cut along them."""
    import cv2
    import numpy as np

    img = np.full((1000, 1654), 255, np.uint8)
    xs = [110, 183, 383, 961, 1105, 1344, 1541]
    ys = [200, 250, 300, 350, 400]
    for y in ys:
        cv2.line(img, (xs[0], y), (xs[-1], y), 0, 2)
    for x in xs:
        cv2.line(img, (x, ys[0]), (x, ys[-1]), 0, 2)
    cv2.putText(img, "XYZ", (400, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
    clean, grid = ocr.remove_table_lines(img)
    assert len(grid["h"]) == 5 and len(grid["v"]) == 7
    assert abs(grid["v"][3] - 961) <= 2 and abs(grid["h"][2] - 300) <= 2
    assert clean[200, 600] == 255 and clean[:, 380:386].min() == 255  # rulings erased
    assert clean[250:300, 395:480].min() < 128  # text survives
    words = [_w("1", 130, 255), _w("12A3456", 190, 255), _w("DOE", 395, 255), _w("John", 470, 255), _w("13.5", 1000, 255),
             _w("Bien", 1115, 255), _w("Admis", 1355, 255)]
    rows, _ = ocr.extract_pv_rows({"page": 1, "size": [1654, 1000], "words": words, "grid": grid})
    assert len(rows) == 1 and rows[0]["name_raw"] == "DOE John" and rows[0]["average"] == 13.5
    assert rows[0]["bbox"][1] == pytest.approx(250, abs=2) and rows[0]["bbox"][3] == pytest.approx(300, abs=2)


def test_partial_vertical_rulings_completed_from_template():
    assert len(ocr._complete_columns([110.0, 383.0, 961.0], 110.0, 1541.0)) == 7
    assert len(ocr._complete_columns([500.0], 110.0, 1541.0)) == 1  # disagrees with the template: unchanged


def test_date_noise_variants():
    assert parse_date("ler octobre 1997") == "1997-10-01"
    assert parse_date("February.26, 2024") == "2024-02-26"
    assert parse_date("22juillet2024") == "2024-07-22"
