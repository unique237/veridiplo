# CLAUDE.md — VeriDiplo (ITU AI for Good Sandbox Hackathon, Cameroon)

> **Read this file in full before writing any code.** It is the single source of truth for this repo.
> You (Claude Code) are the **lead engineer and orchestrator**. You delegate to the subagents in
> `.claude/agents/` and integrate their work. Deadline pressure is extreme: **a working, containerised MVP
> beats a perfect architecture.** Ship vertical slices; keep `docker compose up` green at all times.

---

## 1. What we are building (solution, not tech)

**VeriDiplo** — *Moteur de recherche de preuves documentaires et authentification de diplômes.*

**Problem.** In Cameroon, the evidence that a diploma is genuine is scattered across heterogeneous sources:
scanned deliberation minutes (*procès-verbaux de délibération*, PV), PDF result lists, registry spreadsheets and paper
archives (universities, Office du Baccalauréat, GCE Board, MINESUP). Verifying a diploma is slow (months to years),
manual and error-prone. Fraud is real (≈1,000 gendarmerie trainees dismissed in 2022 over false diplomas).

**Question we answer.** *How can we quickly find the relevant documentary evidence and establish whether the information on a
diploma is consistent with the available institutional sources?*

**What the user gets.** A verifier (employer, ministry, embassy) submits a diploma (scan or typed fields) **plus the holder's consent
token**. Agents find the corroborating records, compare each field, and return:

- a verdict ∈ **{CONSISTENT, INCONSISTENT, INSUFFICIENT_EVIDENCE}** — the system **never outputs "fake/faux"**;
- an **evidence pack** citing source document, page, row and bounding box, with a highlighted image crop;
- the **policy rules** that fired, each linked to a chunk of a real legal text in the open knowledge base;
- any **policy gap** detected (a decision no policy covers).

A **human registrar** approves or rejects every INCONSISTENT verdict before it is communicated.

**The hackathon judges score** (in this order of weight): gap analysis (policy + standards gaps), detailed ITU-T Y.3172
mapping, AI Readiness 2.0 mapping, a real queryable knowledge base, and demo scenarios showing agent behaviour.
**Model accuracy is NOT scored.** So we optimise for: traceability, explainability, policy governance, and the
**Version A (ungoverned) vs Version B (policy-governed)** comparison on identical scenarios.

---

## 2. Non-negotiable rules

1. **Never invent policy text.** Every KB chunk must come from a real, public document listed in `kb/sources.yaml`.
   If a document cannot be downloaded, keep its entry with `status: pending_download`, do not fabricate its content,
   and tell the human which file to drop into `kb/raw/`. Paraphrased summaries must be marked `kind: summary` and cite the
   secondary source URL. Article numbers from secondary sources are tagged `as_reported: true`.
2. **All case data is synthetic.** Fictional institutions only (e.g. "Université Fictive de Nkolbisson"). Every generated
   diploma/PV image carries a visible **"SPECIMEN – SYNTHETIC DATA"** watermark. Never use real people's names, real
   institutions' logos or real stamps/signatures. Do not ingest the MINESUP lists of non-authentic diplomas (they contain real personal data).
3. **No confidential documents** of any ministry or company (disqualification rule).
4. **Personal data stays local** by default: local OCR, local embeddings, `LLM_PROVIDER=none` or `ollama` as default.
   Cloud LLM providers are opt-in via env and must log a "cross-border transfer" warning in the audit log (Law 2024/017, Art. 32 as reported).
5. **The demo must work offline and without any API key** (`LLM_PROVIDER=none` → deterministic templates for explanations).
6. **Everything runs in Docker.** One command: `make up` (wraps `docker compose up --build -d`) then `make seed` then `make demo`.
7. Secrets only in `.env` (git-ignored); commit `.env.example`.
8. Bilingual output (French default, English toggle) for verdicts and UI labels.

---

## 3. Architecture (maps 1:1 to ITU-T Y.3172)

```
Intent (verifier request + consent token) ──► Orchestrator agent
   │
   ▼  Y.3172 pipeline (same code runs as "sandbox" and "production" profile)
 SRC ──► C ──────────────► PP ─────────────────────► M ─────────────────────────► D ──────────► SINK
 PV scans,  Intake/OCR      OCR fr+en, layout,        Evidence Retrieval +          Reporter      Registrar console,
 registries, agent (hash,   chunk (page/row/bbox),   Consistency agents            agent         verifier dashboard,
 diploma     provenance)    ontology extraction      (hybrid search, SPARQL,       (evidence     holder portal
 upload                     → RDF + vectors           SHACL + rules)                pack FR/EN)
        ▲            ▲                  ▲                         ▲
        └──────── P: Policy Guardian + Gap Analyst (policy-as-code YAML ↔ open KB) governs every node
```

### Agents (runtime, inside the app — not to be confused with the build subagents)

| Agent | Y.3172 node | Responsibility |
|---|---|---|
| `Orchestrator` | MLFO / intent | Parses the request, builds the plan, calls agents in order, enforces Policy Guardian checkpoints, writes the audit trail. |
| `IntakeAgent` | SRC → C | Accepts uploads/batches, SHA-256 hash, provenance metadata, file-type routing. |
| `OCRAgent` | PP | OCRmyPDF/Tesseract `fra+eng`, deskew, layout + table extraction of PV tables, page/row chunks with bbox. |
| `ExtractionAgent` | PP | Field extraction (regex + rules first, LLM fallback) → ontology instances (RDF). |
| `EvidenceRetrievalAgent` | M | Hybrid search (Postgres FTS/BM25 + pgvector) + SPARQL over the KG; returns ranked `EvidenceItem`s. |
| `ConsistencyAgent` | M | Field-by-field checks + SHACL validation → verdict + confidence + reasons. |
| `PolicyGuardianAgent` | P | Evaluates `kb/policy_rules/*.yaml` at each checkpoint: `allow / redact / require_consent / block / escalate`. Retrieves the KB chunks that justify the decision. |
| `GapAnalystAgent` | P | When a decision point has no applicable rule, or rules conflict, writes a `PolicyGap` record (also shown in UI). |
| `ReporterAgent` | D | Builds evidence pack (JSON + PDF), bilingual explanation, integrity hash. |
| `RegistrarSimAgent`, `VerifierSimAgent`, `HolderSimAgent` | SINK / scenarios | Simulated institutional actors used by the scenario runner (the human can take over the registrar role in the UI). |

**Policy mode switch:** `POLICY_MODE=off` (Version A) disables the Policy Guardian/Gap Analyst and lets the
Reporter use naive wording (it may say "FAKE"); `POLICY_MODE=on` (Version B, default). The scenario runner executes both.

### Tech stack (keep it boring)

- **Python 3.11**, FastAPI (`api`), background worker with the same image (`worker`, simple DB-backed job queue — no Celery).
- **PostgreSQL 16 + pgvector** (`db`): documents, chunks, embeddings, cases, verdicts, audit log, gap register.
- **Oxigraph** (`kg`, image `ghcr.io/oxigraph/oxigraph`): RDF store + SPARQL for the ontology/KG. Python: `rdflib`, `pyshacl`.
- **OCR**: `ocrmypdf`, `tesseract-ocr-fra`, `tesseract-ocr-eng`, `pdfplumber`, `opencv-python-headless`.
- **Embeddings**: `sentence-transformers` with `intfloat/multilingual-e5-small` (CPU), cached in a volume; fallback to TF-IDF if the model can't download.
- **Fuzzy matching**: `rapidfuzz`, `unidecode` (name normalisation: accents, compound names, surname/given-name order).
- **LLM gateway** (`app/llm/gateway.py`): providers `none | ollama | anthropic | openai`, selected by env. Optional `ollama` service under compose profile `llm` (model `qwen2.5:3b-instruct`).
- **UI**: Streamlit (`ui`) — fastest path to a good demo. Pages listed in §6.
- **Tests**: pytest; scenario tests are the acceptance tests.

### Repository layout (create exactly this)

```
veridiplo/
├─ CLAUDE.md  README.md  Makefile  docker-compose.yml  .env.example  LICENSE (MIT)
├─ .claude/agents/                 # build subagents (already provided)
├─ app/
│  ├─ api/            main.py, routes_{cases,search,kb,audit,scenarios}.py
│  ├─ agents/         orchestrator.py, intake.py, ocr.py, extraction.py, retrieval.py,
│  │                  consistency.py, policy_guardian.py, gap_analyst.py, reporter.py, sims.py
│  ├─ core/           config.py, db.py, models.py (SQLAlchemy), schemas.py (Pydantic), audit.py, hashing.py
│  ├─ kg/             ontology/veridiplo.ttl, shapes/veridiplo-shapes.ttl, kg_client.py, mapping.py
│  ├─ search/         embed.py, hybrid.py
│  ├─ llm/            gateway.py, prompts/ (fr/en templates)
│  └─ policy/         engine.py (YAML rule evaluator), loader.py
├─ ui/                streamlit_app.py, pages/
├─ data_gen/          generate.py (institutions, students, PVs, registries, diplomas, tampered variants), render.py, noise.py
├─ data/              synthetic/ (generated, git-ignored except a small sample), scenarios/S1..S7.yaml
├─ kb/
│  ├─ sources.yaml    # catalogue of real policy documents (provided — extend, never invent)
│  ├─ raw/            # downloaded PDFs/HTML (git-ignored if large; keep a manifest)
│  ├─ chunks/         # machine-readable chunks: one JSONL per source
│  ├─ policy_rules/   # policy-as-code YAML, each rule linked to chunk IDs
│  ├─ gaps/           # gap register exported as JSON/Markdown
│  ├─ framework/      # ITU AI Readiness dimensions.json (vendored from CrashingGuru/ITUAIReadiness, MIT, with attribution)
│  └─ build_kb.py     # download → extract → chunk → embed → load
├─ docker/            Dockerfile.app, Dockerfile.ui
├─ tests/             unit/, scenarios/test_scenarios.py
└─ docs/              architecture.md, y3172_mapping.md, readiness_mapping.md, gaps.md, demo_script.md, screenshots/
```

---

## 4. Data contracts

### 4.1 Ontology (`app/kg/ontology/veridiplo.ttl`, prefix `vd: <https://veridiplo.org/ont#>`)

Classes: `vd:Person`, `vd:Diploma`, `vd:Institution`, `vd:Programme`, `vd:ExamSession`, `vd:Deliberation` (PV),
`vd:DeliberationEntry` (one row of a PV), `vd:Grade`, `vd:Mention`, `vd:Signatory`, `vd:Accreditation`,
`vd:SourceDocument`, `vd:EvidenceItem`, `vd:VerificationCase`, `vd:Verdict`, `vd:PolicyRule`, `vd:PolicyGap`, `vd:LegalProvision`.

Key properties: `vd:holder`, `vd:issuedBy`, `vd:forProgramme`, `vd:inSession`, `vd:recordedIn` (entry → PV),
`vd:serialNumber`, `vd:average`, `vd:mention`, `vd:signedBy`, `vd:inOfficeFrom/To`, `vd:accreditedFrom/To`,
`vd:sourcePage`, `vd:bbox`, `vd:sha256`, `vd:supports` / `vd:contradicts` (EvidenceItem → field), `vd:justifiedBy` (PolicyRule → LegalProvision).
Reuse `schema:` / `dcterms:` / `prov:` where natural (PROV-O for provenance). SHACL shapes encode the consistency constraints below.

### 4.2 Consistency checks (ConsistencyAgent) — each returns `{check_id, field, status: pass|fail|unknown, evidence_ids, detail}`

| ID | Check |
|---|---|
| C1 | Holder name matches a PV entry (normalised: uppercase, unidecode, token-sort; threshold ≥ 92 pass, 80–92 → `unknown` + human review) |
| C2 | Date/place of birth match (if present) |
| C3 | Programme exists at institution and session falls in `Accreditation` period |
| C4 | Average and mention match the PV entry; mention consistent with average bands |
| C5 | Session/year and deliberation date coherent with diploma issue date |
| C6 | Signatory was in office on the signing date |
| C7 | Serial number format valid for institution/year and **unique** (no duplicates in registry) |
| C8 | Diploma image integrity hints (optional, low weight): mismatched fonts/blocks via simple OCR-confidence anomalies — never decisive alone |

Verdict logic: any `fail` on C1/C3/C4/C6/C7 with evidence → `INCONSISTENT`; no PV/registry evidence found → `INSUFFICIENT_EVIDENCE`;
all critical checks `pass` → `CONSISTENT`; `unknown` on a critical check → `INSUFFICIENT_EVIDENCE` + human review. Confidence = weighted pass ratio × OCR confidence.

### 4.3 Policy-as-code (`kb/policy_rules/*.yaml`)

```yaml
- id: PR-CONSENT-001
  checkpoint: before_search            # before_ingest | before_search | before_report | before_share | before_export_abroad
  when: "request.consent_token is null and request.legal_basis is null"
  action: block                        # allow | redact | require_consent | block | escalate
  message_fr: "Recherche refusée : aucun consentement du titulaire ni base légale."
  message_en: "Search refused: no holder consent or legal basis."
  justified_by: [CM-DP-2024#consent, CM-MINESUP-FORM#autorisation]   # KB chunk IDs — must exist
  pipeline_node: P
  readiness_dims: [10]
```

Required rule set (minimum): consent/legal basis; purpose limitation (recruitment, admission, equivalence only);
bulk-query limit (> 20 holders/hour per verifier → block + gap); redaction of non-needed fields in reports;
**no-accusation wording** (forbid "faux/fake/fraud" in outputs; refer to registrar — Penal Code s.219 decided by humans);
mandatory registrar approval for INCONSISTENT; cross-border request → minimal attestation (yes/no + hash) + transfer flag;
retention of uploads 90 days (marked `proposal: true`, it is our proposal, not law); audit of registrar approvals without justification;
cloud-LLM use → transfer warning. The engine evaluates `when` with a safe expression evaluator (no `eval`; use `simpleeval`).

### 4.4 KB chunk format (`kb/chunks/<source_id>.jsonl`)

```json
{"chunk_id":"CM-DP-2024#c012","source_id":"CM-DP-2024","title":"Loi n° 2024/017 ... protection des données à caractère personnel",
 "issuer":"Présidence de la République","date":"2024-12-23","jurisdiction":"CM","binding":true,"kind":"primary|summary",
 "article":"Art. 32","as_reported":false,"lang":"fr","topics":["cross_border","consent"],"pipeline_nodes":["C","P"],
 "readiness_dims":[10,13],"url":"...","text":"...","sha256":"..."}
```

Chunk by article when the text has articles; otherwise ~300–500 tokens with 50 overlap. Store in Postgres (FTS + pgvector) **and**
export JSONL (the KB must be open and machine-readable on its own, independent of our code). Keep it swappable: changing `KB_DIR`
and the ontology module must be enough to switch domain (e.g. civil-status certificates) — this is demo scenario material.

### 4.5 Audit log (append-only table `audit_events`)

`id, ts, case_id, agent, action, input_hash, output_hash, policy_rule_ids[], kb_chunk_ids[], policy_mode, actor (agent|human), detail jsonb`.
Hash-chain each row (`prev_hash`) so tampering is visible. Expose `GET /audit/{case_id}`.

---

## 5. API (FastAPI, port 8000)

- `POST /cases` — create a verification case (multipart: diploma file or JSON fields; `consent_token`, `purpose`, `requester_type`, `requester_country`).
- `POST /cases/{id}/run?policy_mode=on|off` — run the pipeline; returns verdict, checks, evidence, fired rules, gaps.
- `GET /cases/{id}` / `GET /cases/{id}/report.pdf` / `POST /cases/{id}/review` (registrar approve/reject + justification).
- `GET /search?q=&institution=&year=` — evidence search engine over archives (hybrid), returns snippets + page crops.
- `GET /kb/search?q=` — policy KB search; `GET /kb/rules`; `GET /gaps`.
- `POST /ingest` — upload archive documents (PV scans, registries) for an issuer.
- `POST /scenarios/{sid}/run` — runs scenario in A and B, returns side-by-side diff.
- `GET /health`.

---

## 6. UI (Streamlit, port 8501) — bilingual toggle in sidebar

1. **Vérifier un diplôme** — upload/fields + consent + purpose → timeline of agent steps (live), verdict badge, checks table, evidence crops with highlights, fired policy rules (click → KB text), gaps.
2. **Recherche de preuves** — search engine over archives (filters: institution, year, programme), results with page thumbnails.
3. **Console du registraire** — queue of INCONSISTENT / review cases; approve/reject with mandatory justification.
4. **Base de connaissances** — browse/search KB chunks and policy rules; show source URL and binding status.
5. **Scénarios A/B** — pick S1–S7, run both modes, side-by-side output + diff of behaviour. **This page is the demo centrepiece.**
6. **Audit & écarts** — audit trail per case (hash chain verified ✔), gap register with export to Markdown.
7. **Pipeline Y.3172** — static diagram showing which agent sits on which node + live counters.

Make it look clean: consistent colours (green CONSISTENT, red INCONSISTENT, amber INSUFFICIENT), wide layout, a header "VeriDiplo – SPECIMEN DATA".

---

## 7. Synthetic data (data_gen)

- 5 fictional institutions (mix francophone/anglophone, one private), 20 programmes with accreditation periods, signatories with terms of office.
- ~600 graduates across sessions 2015–2025; Cameroonian-style fictional names (compound names, both orders), plausible averages/mentions.
- Per session: **PV PDFs** rendered with reportlab, then degraded to look scanned (rotation ±2°, noise, blur, JPEG artefacts, 200–300 dpi); registry CSV/XLSX; graduation list PDFs.
- **Diplomas**: genuine specimens + tampered variants: altered mention/average, altered name, wrong session date, programme not yet accredited,
  signatory out of office, duplicated serial, plus a "missing archive" case (1998 PV absent) and an "OCR-hard name" case.
- All with the SPECIMEN watermark. Seeded RNG for reproducibility (`SEED=42`). `make seed` generates + ingests everything.

## 8. Demo scenarios (must exist as `data/scenarios/S*.yaml` and pytest acceptance tests)

| ID | Input | Expected A (off) | Expected B (on) |
|---|---|---|---|
| S1 | Genuine Licence, consent present | CONSISTENT | CONSISTENT + citations + audit |
| S2 | Mention Passable→Bien, average raised | text contains "FAKE" | INCONSISTENT, no accusation, escalated to registrar, holder notified |
| S3 | HR firm, 200 names, no consent | returns records | BLOCKED (PR-CONSENT, PR-BULK) + gap P5 |
| S4 | Request from UAE employer via embassy | full transcript returned | minimal attestation + transfer flag (Art. 32) + comparative gap |
| S5 | 1998 PV missing | guesses (similar name) | INSUFFICIENT_EVIDENCE + archive request draft + gap P2 |
| S6 | OCR-hard / reversed name order | false INCONSISTENT | `unknown` → human review, rectification path |
| S7 | Registrar approves without justification | accepted | rejected + supervisor alert |

`make demo` runs all scenarios in both modes and writes `docs/ab_results.md` (table + key quotes) — **this file feeds the report and video.**

---

## 9. Build plan (work in this order; commit after each milestone with a clear message)

- **M0 – Skeleton (infra-devops):** compose (db, kg, api, worker, ui, optional ollama), Dockerfiles, Makefile, `/health`, empty Streamlit. ✅ when `make up` is green.
- **M1 – Data & KB (synthetic-data, policy-kb, kg-ontology — in parallel):** generator + ontology + SHACL + `kb/build_kb.py` + rule YAMLs.
- **M2 – Ingestion & search (ingestion-ocr, retrieval):** OCR → chunks → embeddings → hybrid search; `/search` + UI page 2.
- **M3 – Verification & governance (retrieval-verification, agents-runtime):** consistency checks, verdicts, Policy Guardian, Gap Analyst, audit chain, A/B mode.
- **M4 – UI & scenarios (frontend-ui, qa-scenarios):** all pages, scenario runner, acceptance tests, `docs/ab_results.md`, screenshots in `docs/screenshots/`.
- **M5 – Docs (docs-writer):** README (quick start in 3 commands, architecture, KB description, licence), `docs/*mapping.md`, `docs/gaps.md`, `docs/demo_script.md` (≤7 min video script).

If time runs short, cut in this order: C8 image-integrity check → holder portal → PDF report (keep JSON) → Ollama profile. **Never cut**: KB + policy rules, A/B scenarios, evidence citations, audit log.

## 10. How to delegate

Use the subagents in `.claude/agents/` — run independent ones **in parallel** (M1 especially). Give each one: the milestone,
the files it owns, and the data contracts in §4. Each subagent must finish with: files changed, how to run/test, open issues.
You integrate, run `make up && make seed && make test`, and fix integration bugs yourself. Do not let two subagents edit the same file at once.

## 11. Commands

```
make up        # docker compose up --build -d
make seed      # generate synthetic data + build KB + ingest archives + load KG
make test      # pytest inside api container
make demo      # run S1–S7 in A and B, write docs/ab_results.md
make down / make logs / make reset
```

## 12. Team

Mentor: Arlegil Youmba Tchokothe · Members: Mbame Mbame Martin James, NANGOU POUNTOUGNIGNI Marilyn, TONFACK MATENGUE Elvira Brenda.
Commit messages in English, imperative mood. Code comments in English; user-facing text in French with English translations.
