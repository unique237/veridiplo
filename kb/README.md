# VeriDiplo open knowledge base (`kb/`)

A machine-readable, **real-text-only** knowledge base of the legal, policy and standards documents that govern diploma
verification in Cameroon, plus comparison texts (EU, UAE, AU, ECOWAS). It is independent of the application code: the JSONL files
can be used on their own; Postgres (FTS + pgvector) loading is optional.

> Rule (CLAUDE.md §2.1): never invent policy text. Every chunk comes from a document listed in `sources.yaml`. A document
> that could not be downloaded keeps `status: pending_download`, has **no** chunks, and is listed below for a human drop-in.

## Contents (snapshot 11 Nov 2026 — regenerate with `python kb/build_kb.py`, see `COVERAGE.md`)

* **31 catalogued sources, 26 with text, 964 chunks, 56 semantic anchors**; 5 sources pending a manual drop-in.
* Education sector: Cameroon Higher-Education Law 2001 (repealed, historic), MINESUP equivalence form (holder authorisation),
  press on the 2024 national verification platform (summary), Addis Ababa Recognition Convention (UN record), UNESCO/UAE
  recognition practice (summary).
* AI and data policy: Cameroon Law 2024/017 (data protection, **OCR of the Presidency's certified copy**), Law 2010/012 (cybersecurity),
  Law 2010/021 (e-commerce), Law 2024/001 + 2000/010 (archives), Penal Code articles on forgery/diploma, SNIA (summary),
  AU AI Strategy, AU Data Policy Framework, Malabo Convention (OCR), ECOWAS Supplementary Act, NIST AI RMF 1.0, OECD AI Principles (summary),
  EU GDPR / AI Act (selected articles, comparison only), UAE PDPL (summary).
* Standards: ITU-T Y.3172, ITU AI Readiness 2.0 (13 dimensions), W3C VC 2.0, SHACL, PROV-O.

| Path | What |
|---|---|
| `sources.yaml` | catalogue: url, issuer, date, `binding`, `kind`, licence, `anchors`, chunking/extraction options, actual `status`, `raw_file`, `chunks` |
| `build_kb.py` | download -> extract -> chunk -> JSONL (-> optional DB). Idempotent. Prints the coverage table |
| `tagging.yaml` | keyword tagging of topics / Y.3172 nodes / readiness dimensions (swappable per domain) |
| `raw/` | downloaded originals + `MANIFEST.json` (url, sha256, date, HTTP status). Originals are git-ignored |
| `chunks/<source_id>.jsonl` | the chunks; `chunks/anchors.json` {anchor -> chunk_id}; `chunks/_status.json` |
| `COVERAGE.md` | generated coverage table |
| `policy_rules/` | policy-as-code (`README.md` documents the evaluation contract; `context_schema.yaml`, `examples.yaml`) |
| `gaps/` | `seed_gaps.yaml`, `export_gaps.py`, generated `gap_register.json/.md` |
| `framework/dimensions.json` | ITU AI Readiness 2.0 dimensions (vendored, MIT, see attribution) |

## Chunk format (CLAUDE.md §4.4)

```json
{"chunk_id":"CM-DP-2024#c014","source_id":"CM-DP-2024","title":"Loi n° 2024/017 ...","issuer":"Présidence de la République du Cameroun",
 "date":"2024-12-23","jurisdiction":"CM","binding":true,"kind":"primary","article":"Art. 9","as_reported":false,"lang":"fr",
 "topics":["consent","data_protection"],"pipeline_nodes":["SRC","C","P"],"readiness_dims":[1,10],"url":"...","text":"...","sha256":"...",
 "section":"...","page":7,"part":null,"extraction":"ocr","licence":"...","anchors":["consent"]}
```

* `binding` = binding **on Cameroon** (comparison texts and non-ratified treaties are `false`).
* `kind: summary` chunks come from a secondary/overview page; they carry `as_reported: true`; article numbers are never given for them.
* `extraction`: `pdf_text | ocr | html | json`. OCR text can contain errors — verify against the official publication before legal use.
* `page` is the PDF page index; `article` is "Art. N" (laws), "Section N" (the English cybersecurity law, W3C), "Clause N" (ITU), "Annex III", "Dimension N".
* Chunking: by article (regex `Article|ARTICLE|Art.` at line start; `Section N` where the text uses it); articles above ~520 words are split with 40-word
  overlap; non-article documents use ~330-word (~440 token) windows with overlap; standards use heading-based sections.
* **Anchors** (`CM-DP-2024#consent`, `#transfer`, `#automated_decision`...) are stable semantic names created only when a regex declared in
  `sources.yaml` matches the text of a real chunk. Rules cite anchors or chunk ids; `app/policy/loader.py::resolve_ref()` resolves both.

## Build, test, load

```bash
python kb/build_kb.py                 # download (robots-aware, timeout 10/90 s, one retry), extract, chunk, write JSONL, coverage table
python kb/build_kb.py --offline       # rebuild chunks from kb/raw only
python kb/build_kb.py --only CM-DP-2024 --retry-failed
python kb/build_kb.py --sync-status   # write actual status/raw_file/chunks back into sources.yaml
python kb/build_kb.py --load-db       # upsert chunks into Postgres via app.core.models.KBChunk (+ embeddings if sentence-transformers is installed)
python kb/gaps/export_gaps.py         # merge seed + runtime gaps (--runtime file.json | --db) into gap_register.json/.md
python -m app.policy.loader           # rule/chunk integrity report (validate_rules)
pytest tests/unit/test_policy_rules.py tests/unit/test_kb_chunks.py
```

* Standalone dependencies: `requests beautifulsoup4 pyyaml pdfplumber pypdf` (+ `lxml` optional). Scanned PDFs need `ocrmypdf` or `tesseract` with
  `gs`/`pdftoppm`; without them the source is reported `pending_ocr` (no text is invented).
* `load_to_db()` does **not** create tables: call `app.core.db.init_db()` first (creates `kb_chunks` with the vector column and the FTS index). The DB mirrors
  the ORM columns only; `section/page/part/anchors/licence` stay in the JSONL.
* Idempotent: unchanged inputs give byte-identical JSONL (checked by hashing `chunks/*.jsonl` across two runs). Downloads are skipped when the raw file and manifest entry exist.
* Crawling etiquette: robots.txt is honoured (this is why the ANTIC copy of Law 2010/012 is *cited* but not fetched: the host disallows `/images/`; a byte-identical copy
  hosted by the UNIDIR Cyber Policy Portal is used). Cloudflare-protected sites (unesdoc.unesco.org, unesco.org, iso.org, coe.int) answer HTTP 403 to the build — we do not
  try to bypass such protection; those sources are pending drop-in.

### Special extraction options used
* `CM-DP-2024`: scanned certified copy with a diagonal "www.prc.cm" watermark -> `ocr_threshold: 140` (binarise at 300 dpi, then tesseract `fra+eng`, psm 4).
  Plain OCR garbled several articles; thresholding recovered all Art. 1-75.
* `INT-ADDIS-2014`: 4-language UN record; only English pages 1-2 (registration + list of Parties) and 12-22 (treaty text) are chunked.
* `CM-PEN-2016`: the WIPO Lex page holds the whole Penal Code (OCR-quality French, accents missing); only the forgery/diploma articles 144, 206, 219, 219-1, 220, 314, 315 are kept.
* `EU-GDPR-2016`, `EU-AI-ACT-2024`: only the articles relevant to consent, purpose, automated decisions, transfers, oversight, record-keeping (+ AI Act Annex III) are kept.

## Licences and provenance of each source

Details per source are in `sources.yaml` (`licence`, `redistribute`). Summary:

| Group | Sources | Licence / reuse |
|---|---|---|
| Cameroonian official texts | CM-DP-2024, CM-CYB-2010, CM-ECOM-2010, CM-ARCH-2024/2000, CM-HE-2001, CM-PEN-2016, CM-MINESUP-FORM | Official legislative/administrative texts, public; copies hosted by third parties are named in `licence` (digitalbusiness.africa copy of the Presidency's certified copy, UNIDIR portal, campusunivers.com, WIPO Lex) |
| Press / overview pages (`kind: summary`) | CM-SNIA-2025, CM-VERIF-PLATFORM-2024, OECD-AI-2019, UAE-PDPL-2021, UAE-MOHESR-RECOG | third-party web pages: short excerpts for citation only (`redistribute: false`) |
| AU / UN / ECOWAS | INT-ADDIS-2014, INT-MALABO-2014, ECOWAS-DPA-2010, AU-AI-2024, AU-DATA-POLICY-2022 | treaty texts public; AU strategy/framework: no open licence stated (excerpts) |
| EU | EU-GDPR-2016, EU-AI-ACT-2024 | reuse under Commission Decision 2011/833/EU; **comparison only, not applicable in Cameroon** |
| NIST | NIST-AI-RMF-1.0 | US Government work, public domain |
| ITU | ITU-Y3172 | © ITU 2019, free single-copy download; excerpts for citation, no redistribution of the PDF |
| W3C | W3C-VC-2.0, W3C-SHACL, W3C-PROV-O | W3C Document License (copy with attribution) |
| ITU AI Readiness | ITU-READINESS-2 | `framework/dimensions.json`: MIT, Copyright (c) 2026 Vishnu Ram OV (repository CrashingGuru/ITUAIReadiness), data describing the ITU report "AI Ready — Analysis Towards a Standardized Readiness Framework", Report 2.0, January 2026 |

**Attribution (MIT).** `kb/framework/dimensions.json` is vendored unchanged from
https://github.com/CrashingGuru/ITUAIReadiness (`simulation/data/framework/dimensions.json`). MIT License, Copyright (c) 2026 Vishnu Ram OV.
The permission notice of the upstream LICENSE applies to that file. Before any public release, check that excerpts of non-open sources (`redistribute: false`) may be published.

`binding` caveats: INT-ADDIS-2014 is `binding: false` for Cameroon because the UN registration record lists 12 Parties and Cameroon is not one of them (it appears on the
signature pages; current status must be checked). INT-MALABO-2014 is `binding: true` in the catalogue as an AU treaty text but Cameroon's ratification was **not verified**
(see gap P10). GDPR/AI Act/ECOWAS/Lisbon are comparison texts (`binding: false`).

## Documents the human must drop into `kb/raw/` (pending)

| File to create | Source | Why it is missing |
|---|---|---|
| `kb/raw/CM-HE-2023.pdf` | Loi n° 2023/007 du 25 juillet 2023 (orientation de l'enseignement supérieur) — prc.cm / minesup.gov.cm / Journal officiel | no public full text found |
| `kb/raw/INT-UNESCO-ETH-2021.pdf` (or `.html` of the unesco.org page) | https://unesdoc.unesco.org/ark:/48223/pf0000381137 | HTTP 403 (Cloudflare) |
| `kb/raw/INT-GLOBAL-CONV-2019.pdf` | https://unesdoc.unesco.org/ark:/48223/pf0000374905_eng | HTTP 403 (Cloudflare) |
| `kb/raw/INT-LISBON-1997.pdf` | https://www.coe.int/en/web/conventions/full-list?module=treaty-detail&treatynum=165 | HTTP 403 |
| `kb/raw/ISO-IEC-42001.*` | https://www.iso.org/standard/81230.html — **paywalled standard: do not commit it**; only your own short notes citing clause numbers | HTTP 403 / paywall |
| optional upgrades | `CM-SNIA-2025.pdf` (official SNIA PDF, then set `kind: primary`), `OECD-AI-2019.pdf` (legal instrument OECD/LEGAL/0449), `UAE-PDPL-2021.pdf` | only overview pages available |

After dropping a file: `python kb/build_kb.py --offline --only <ID> --sync-status` (a manual file named `<source_id>.<ext>` takes priority over any URL; `local_file:` can name another file).

## Add a source

1. Append an entry to `sources.yaml` (real URL only; `binding`, `kind`, `licence`, `lang`, `date`, `issuer`; `chunking.mode`: `article | tokens | headings`).
2. `python kb/build_kb.py --only NEW-ID` (or drop the file in `raw/`). Inspect `chunks/NEW-ID.jsonl`.
3. Add `anchors:` (regex matching real chunk text) for ideas that rules or gaps cite; re-run; unresolved anchors are reported.
4. Reference it from rules (`justified_by`) or gaps (`law_cited`); `pytest tests/unit/test_policy_rules.py` must stay green.

## Swap the KB for another domain (e.g. civil-status certificates)

Set `KB_DIR=/path/to/other_kb` (the build, `app/policy/loader.py`, the engine and the API read it). The new directory needs: `sources.yaml`,
`tagging.yaml` (topics/nodes/dimensions), `policy_rules/*.yaml` + `context_schema.yaml` (same namespaces, or extend it), `gaps/seed_gaps.yaml`, and optionally `framework/`.
Switch the ontology module (`app/kg/ontology/`) and scenarios accordingly; nothing else in the code is diploma-specific.
