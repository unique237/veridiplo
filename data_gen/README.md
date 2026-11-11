# data_gen - synthetic data for VeriDiplo

**All data is fictional.** Institutions, people, stamps and signatures are invented; every PDF/PNG carries the diagonal
watermark `SPECIMEN – SYNTHETIC DATA` (no real logos, stamps or signatures).

```bash
python -m data_gen                        # = python -m data_gen.generate ; output: $DATA_DIR/synthetic (default data/synthetic)
python -m data_gen.generate --out data/synthetic [--seed 42] [--workers 4] [--no-render]
python -m data_gen.validate --out data/synthetic   # re-derives checks C1/C3/C4/C5/C6/C7 from the sources and compares with expected outcomes
```

Seeded (`SEED` env, default 42), byte-for-byte reproducible (PDF/PNG/XLSX included). Runtime about 40-60 s on 4 cores
(about 130 s CPU in total, so < 3 min even on one core). Needs only: reportlab, pillow, numpy, opencv-python-headless, pymupdf,
openpyxl, pyyaml (pandas optional).

## Output (`data/synthetic/`)

| Path | Content |
|---|---|
| `institutions.json` | 5 institutions (UFN, UFB, FUM = public; ISFG = public; IPFM = private, bilingual), 20 programmes with accreditation periods, signatories with terms, serial format regex |
| `graduates.json` | ground truth, 590 graduates (579 in 2015-2025 + 11 in the 1998 "lost archive" cohort). Extra keys: `language`, `accredited_at_session`, `archive_available`, `pv_file`, `pv_row` |
| `pv/<INST>_<PROG>_<YEAR>.pdf` | **scanned** PV (image-only, rotated +-2 deg, blur, noise, JPEG artefacts, 200-300 dpi) |
| `pv/<...>.clean.pdf` | same PV with text layer (tests only) |
| `pv_index.json` | every PV row as rendered (name order, page, `bbox_pt` in PDF points top-left origin of the clean PDF), scan parameters |
| `registries/<INST>_<YEAR>.csv` / `.xlsx` | graduate registries with nulls, mixed date/decimal formats, case/whitespace noise. `.xlsx` (UFN_2020, FUM_2022, IPFM_2024) replace the CSV of that year and have 3 title rows + blank row before the header; ISFG CSVs use `;` and decimal commas. Last column `avertissement` carries the specimen notice |
| `lists/<INST>_<YEAR>.pdf` | digital "liste des admis" (text PDFs) |
| `diplomas/<case_id>.png/.json` | 40 diploma specimens (FR/EN) + typed fields |
| `ground_truth.json` | per diploma: label, status, expected verdict/checks, true record, evidence pointer (PV file/row, registry) |
| `manifest.json` | counts, seed, missing archives, per-case expected verdict/failing checks, registry file inventory |

Scenario fixtures: `data/scenarios/S1.yaml ... S7.yaml` (paths relative to `DATA_DIR`). Committed sample: `data/sample/`.

## Design notes

* PV columns: `N°, Matricule, Nom et prénoms, Moyenne, Mention, Décision` (English programmes add an English sub-label under each header
  cell). Rows = admitted candidates (`Admis`/`Admitted`) **plus** non-admitted ones (`Ajourné`/`Failed`, mention `-`) that are not in
  `graduates.json`. Names are `FAMILY Given` (~88 %) or `Given FAMILY`. Averages use a dot (`13.45`); registries are messier.
* Mentions: Passable 10-11.99, Assez Bien 12-13.99, Bien 14-15.99, Très Bien >= 16 (EN: Pass, Fairly Good, Good, Very Good).
* Accreditation is evaluated at the PV `deliberation_date`. A few genuine-looking cohorts ran outside an accreditation window
  (e.g. UFB LLOG before 2019-09-01); they are the source of the `not_accredited` diplomas.
* Missing archive: UFN LDROIT 1998 has graduates but **no** PV, registry or list (see `manifest.missing_archives`). `MISS-001`'s
  holder has a look-alike (same surname and first given name) in a later UFN PV, to tempt "guessing" in version A.
* Tampers are detectable only against sources: `TMEN` (C4 mention, + average raised for TMEN-001), `TAVG` (C4), `TNAM` (C1),
  `WSES` (C5, C1 if search is scoped to the claimed session), `NACC` (C3), `SOOO` (C6), `DUPS` (C7, the serial appears twice in
  the registry), `MISS` (no evidence -> INSUFFICIENT_EVIDENCE), `OCRH` (name similarity 80-92 -> C1 `unknown`), `REVN` (given names first on the diploma, must still pass).
* Scenario tags: S1=GEN-001, S2=TMEN-001, S4=GEN-002, S5=MISS-001, S6=OCRH-001, S7=TMEN-002; S3 is a 200-item bulk request.
