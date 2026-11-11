"""Self-check: re-derive the consistency checks from the generated *sources* (PV index, registries,
accreditation and signatory data) and compare them with the expected outcomes of every diploma case.

    python -m data_gen.validate --out data/synthetic
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from .names import norm_name, token_sort_ratio


def _load(out: Path, name: str):
    return json.loads((out / name).read_text(encoding="utf-8"))


def _read_registry(path: Path) -> list[dict]:
    if path.suffix == ".xlsx":
        from openpyxl import load_workbook
        ws = load_workbook(path, read_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        hi = next(i for i, r in enumerate(rows) if r and r[0] == "matricule")
        hdr = list(rows[hi])
        return [dict(zip(hdr, r)) for r in rows[hi + 1:] if r and r[0] and not str(r[0]).startswith("Total")]
    text = path.read_text(encoding="utf-8")
    delim = ";" if text.split("\r\n", 1)[0].count(";") > 3 else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delim))


def run_checks(out: Path) -> list[str]:
    errors: list[str] = []
    insts = {i["id"]: i for i in _load(out, "institutions.json")}
    grads = _load(out, "graduates.json")
    pvi = _load(out, "pv_index.json")
    manifest = _load(out, "manifest.json")
    gt = _load(out, "ground_truth.json")
    progs = {p["id"]: p for i in insts.values() for p in i["programmes"]}
    if len(insts) != 5 or len(progs) != 20:
        errors.append(f"expected 5 institutions / 20 programmes, got {len(insts)} / {len(progs)}")
    # ---- graduates
    bands = [(10, 12), (12, 14), (14, 16), (16, 20.01)]
    serials, matr = set(), set()
    for g in grads:
        inst = insts[g["institution_id"]]
        if not re.match(inst["serial_format"], g["diploma_serial"]):
            errors.append(f"serial format {g['person_id']} {g['diploma_serial']}")
        if g["diploma_serial"] in serials:
            errors.append(f"duplicate serial in ground truth {g['diploma_serial']}")
        serials.add(g["diploma_serial"])
        if g["matricule"] in matr:
            errors.append(f"duplicate matricule {g['matricule']}")
        matr.add(g["matricule"])
        lang = g["language"]
        ments = manifest["mention_bands"][lang]
        lo, hi = bands[ments.index(g["mention"])]
        if not (lo <= g["average"] < hi):
            errors.append(f"mention/average mismatch {g['person_id']}")
        sg = next(s for s in inst["signatories"] if s["id"] == g["signatory_id"])
        if not (sg["in_office_from"] <= g["issue_date"] and (sg["in_office_to"] is None or g["issue_date"] <= sg["in_office_to"])):
            errors.append(f"signatory out of office for genuine graduate {g['person_id']}")
        if g["issue_date"] <= g["deliberation_date"]:
            errors.append(f"issue before deliberation {g['person_id']}")
        if g["archive_available"] != (g["pv_file"] is not None):
            errors.append(f"archive flag {g['person_id']}")
        if g["pv_file"] and not (out / g["pv_file"]).exists():
            errors.append(f"missing PV file {g['pv_file']}")
        if g["pv_file"] and not (out / g["pv_file"].replace(".pdf", ".clean.pdf")).exists():
            errors.append(f"missing clean PV {g['pv_file']}")
    for m in manifest["missing_archives"]:
        if (out / m["file"]).exists():
            errors.append(f"{m['file']} should be missing")
    # ---- registries (parsed from files)
    reg_rows: dict[str, list[dict]] = {}
    for rf in manifest["registry_files"]:
        reg_rows[rf["file"]] = _read_registry(out / rf["file"])
        if len(reg_rows[rf["file"]]) != rf["rows"]:
            errors.append(f"registry row count {rf['file']}")
    all_serials: dict[str, int] = {}
    for rows in reg_rows.values():
        for r in rows:
            s = (r.get("numero_diplome") or "").strip().upper()
            if s:
                all_serials[s] = all_serials.get(s, 0) + 1

    # ---- cases: re-derive checks from sources
    def acc_ok(prog_id: str, day: str) -> bool:
        return any(a["from"] <= day and (a["to"] is None or day <= a["to"]) for a in progs[prog_id]["accreditations"])

    def best_row(name: str, key: str):
        if key not in pvi:
            return None, None
        best, br = -1.0, None
        for r in pvi[key]["rows"]:
            sc = token_sort_ratio(name, r["name"])
            if sc > best:
                best, br = sc, r
        return best, br

    for cid, exp in manifest["cases"].items():
        d = _load(out, f"diplomas/{cid}.json")
        key = f"{d['institution_id']}_{d['programme_id']}_{d['session_year']}"
        failing, unknown = set(), set()
        score, row = best_row(d["holder_name"], key)
        pv = pvi.get(key)
        if pv is None:
            verdict = "INSUFFICIENT_EVIDENCE"
        else:
            if score >= 92:
                pass
            elif score >= 80:
                unknown.add("C1")
            else:
                failing.add("C1")
            if not acc_ok(d["programme_id"], pv["deliberation_date"]):
                failing.add("C3")
            if row is not None and score >= 92:
                if abs(float(row["average"]) - d["average"]) > 0.001 or row["mention"] != d["mention"]:
                    failing.add("C4")
            ments = manifest["mention_bands"]["en" if d["language"] == "en" else "fr"]
            lo, hi = bands[ments.index(d["mention"])]
            if not (lo <= d["average"] < hi):
                failing.add("C4")
            if d["issue_date"] <= pv["deliberation_date"]:
                failing.add("C5")
            # session coherence: holder found in another session's PV of the same institution/programme?
            if "C1" in failing:
                for k2, p2 in pvi.items():
                    if p2["institution_id"] == d["institution_id"] and p2["programme_id"] == d["programme_id"] and k2 != key:
                        sc2, _ = best_row(d["holder_name"], k2)
                        if sc2 >= 92:
                            failing.add("C5")
            sg = next((s for s in insts[d["institution_id"]]["signatories"] if s["name"] == d["signatory_name"]), None)
            if sg is None or not (sg["in_office_from"] <= d["issue_date"] and (sg["in_office_to"] is None or d["issue_date"] <= sg["in_office_to"])):
                failing.add("C6")
            ser = d["serial"].strip().upper()
            if not re.match(insts[d["institution_id"]]["serial_format"], d["serial"]) or all_serials.get(ser, 0) > 1:
                failing.add("C7")
            verdict = "INCONSISTENT" if failing & {"C1", "C3", "C4", "C5", "C6", "C7"} else ("INSUFFICIENT_EVIDENCE" if unknown else "CONSISTENT")
        # wrong_session_date: C1 is only a secondary effect (name found elsewhere) -> compare as sets with tolerance
        expected_fail = set(exp["expected_failing_checks"])
        if exp["expected_verdict"] != verdict:
            errors.append(f"{cid}: verdict {verdict} != expected {exp['expected_verdict']} (failing={sorted(failing)}, unknown={sorted(unknown)}, score={score})")
        if exp["label"] == "wrong_session_date":
            if "C5" not in failing:
                errors.append(f"{cid}: C5 should fail")
        elif failing != expected_fail:
            errors.append(f"{cid}: failing {sorted(failing)} != expected {sorted(expected_fail)}")
        if unknown != set(exp["expected_unknown_checks"]) and exp["label"] != "missing_archive":
            errors.append(f"{cid}: unknown {sorted(unknown)} != expected {exp['expected_unknown_checks']}")
        if exp["label"] == "missing_archive" and pv is not None:
            errors.append(f"{cid}: PV should be missing")
        if cid not in gt:
            errors.append(f"{cid} missing in ground_truth.json")
    # ---- counts
    labels = [c["label"] for c in manifest["cases"].values()]
    for lab in ["tampered_mention", "tampered_average", "tampered_name", "wrong_session_date", "not_accredited",
                "signatory_out_of_office", "duplicate_serial", "missing_archive", "ocr_hard_name", "reversed_name_order"]:
        if labels.count(lab) < 2:
            errors.append(f"fewer than 2 cases of kind {lab}")
    # ---- watermark presence in clean PVs (text layer) and image diplomas exist
    try:
        import fitz
        for k in list(pvi)[:5]:
            with fitz.open(out / f"pv/{k}.clean.pdf") as doc:
                if "SYNTHETIC DATA" not in doc[0].get_text():
                    errors.append(f"watermark text missing in {k}")
            with fitz.open(out / f"pv/{k}.pdf") as doc:
                if doc[0].get_text().strip():
                    errors.append(f"scanned PV {k} still has a text layer")
    except ImportError:
        pass
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("DATA_DIR", "data")) / "synthetic"))
    a = ap.parse_args(argv)
    errs = run_checks(Path(a.out))
    for e in errs:
        print("FAIL", e)
    print(f"[validate] {'OK' if not errs else str(len(errs)) + ' problem(s)'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
