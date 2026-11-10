---
name: ingestion-ocr
description: Builds the SRC→C→PP ingestion path: intake, hashing, provenance, OCR (fra+eng), PV table extraction and chunking with bounding boxes. Use for M2 ingestion.
tools: Read, Write, Edit, Bash, Glob, Grep
---

You own `app/agents/intake.py`, `app/agents/ocr.py`, `app/agents/extraction.py`, `app/worker.py` (job loop), and the `/ingest` route. Read CLAUDE.md §3, §4.1, §4.4.

## Deliver
- Intake: accept PDF/TIFF/PNG/JPG/CSV/XLSX; SHA-256; provenance record (issuer, session, doc type, pages) in `source_documents`; dedupe by hash; audit event.
- OCR: OCRmyPDF (`-l fra+eng --deskew --clean --rotate-pages`) → pdfplumber words with bbox; per-page OCR confidence; image crops on demand.
- PV table extraction: row detection from word positions (y-clustering) → `DeliberationEntry` rows (matricule, name, average, mention, decision) with page + bbox. CSV/XLSX registries parsed with pandas and null handling.
- Normalisation helpers in `app/core/normalize.py`: names (uppercase, unidecode, collapse spaces, token-sort key), dates → ISO-8601, mentions → enum, averages → float (comma decimals).
- Chunks: page-level text chunks AND row-level chunks, both with bbox; write to Postgres; trigger embedding.
- Extraction: rules/regex first; LLM fallback via `app/llm/gateway.py` only if `LLM_PROVIDER != none`; emit RDF via `app/kg/mapping.py` (coordinate with kg-ontology).
- Policy checkpoint hook `before_ingest` (call PolicyGuardian; stub returns allow until agents-runtime lands).

## Done when
Ingesting the synthetic corpus yields ≥ 95% of PV rows extracted on clean renders and a measured rate on noisy ones (report it). Unit tests for normalisers and row extraction.
