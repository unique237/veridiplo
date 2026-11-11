# ITU AI Readiness 2.0 mapping — what VeriDiplo implements, what it does not

Framework: *ITU AI Ready — Analysis Towards a Standardized Readiness Framework, Report 2.0, January 2026*, machine-readable in
`kb/framework/dimensions.json` (vendored from CrashingGuru/ITUAIReadiness, MIT, Copyright (c) 2026 Vishnu Ram OV — attribution in `kb/README.md`).
13 dimensions, 6 factors (Data, Research, Deployment Support, Standards, Open Source and Code, Sandbox Environments). Metric ids (`dN_mMM`) are those of the JSON; each
dimension also exists as KB chunk `ITU-READINESS-2#dimNN` (anchors `dim01`..`dim13`) so rules, gaps and the UI can link to it.

> **Honest scope.** VeriDiplo is a hackathon sandbox on **synthetic data**. It does not "score" Cameroon's readiness: for each dimension it states which of the
> framework's metrics the prototype *addresses by design*, where the evidence lives in the repository, and what is not addressed. Nothing here is a measured national indicator.
> Ratings: **Strong** = designed and testable now; **Partial** = some metrics addressed; **Minimal** = marginal; **Not addressed**.
> Chunk-level `readiness_dims` tags in the KB are keyword-based (coarse); the authoritative links are the `readiness_dims` of each policy rule and this document.

| # | Dimension | Rating | Policy rules tagged | Main evidence |
|--:|---|---|---|---|
| 1 | Data/Model Marketplace | Partial | CONSENT-001/002/003, PURPOSE-001, BULK-001, REDACT-001, RETENTION-001 | `kb/`, `data_gen/`, `app/kg/` |
| 2 | Generated Content Marketplace | Minimal | — | `app/llm/gateway.py`, PR-NOACCUSE-001 |
| 3 | Cross-Domain Correlation Analysis | Partial | EVIDENCE-001 | `app/agents/consistency.py`, `app/kg/` |
| 4 | Contextualization and Regional Impact | Partial | — | `data_gen/names.py`, Cameroon KB sources |
| 5 | Level of Integration of AI in Workflows | Partial | — | A/B runner, `docs/y3172_mapping.md` |
| 6 | Human Interface | Partial | NOACCUSE-001 | bilingual FR/EN outputs, `ui/` |
| 7 | Strategy Alignment | Partial | CROSSBORDER-001/002, LLM-TRANSFER-001 | scenario S4, SNIA/AU/UAE KB sources |
| 8 | Collaboration with AI | Strong (human gate) | HUMAN-REVIEW-001, REGISTRAR-001 | registrar console, scenarios S2/S6/S7 |
| 9 | Impacts of Humans in AI Integration | Minimal | — | explanations; no training programme |
| 10 | AI & Policies | **Strong** | all 16 rules | `kb/policy_rules/`, `kb/gaps/`, `app/policy/` |
| 11 | AI for Inclusion | Partial | HOLDER-NOTICE-001 | offline/local processing, holder rights |
| 12 | Granular Priorities | Minimal | — | domain-swap via `KB_DIR`, diploma-fraud focus |
| 13 | Digital Infrastructure | Partial | — | Docker/CPU-only stack, Y.3172 node mapping |

## Dimension by dimension

### 1. Data/Model Marketplace — *Partial*
Framework definition: ecosystem of (un)structured data, expert knowledge base and platform for value providers.
* **Addressed.** d1_m03 *Data Collection Sources* — heterogeneous synthetic sources (PV scans, result lists, registries; flagged synthetic) in `data_gen/`, with provenance and SHA-256 (`app/core/hashing.py`, PROV-O);
  d1_m09 *Privacy and Security* — consent gate (PR-CONSENT-*), purpose limit, redaction (PR-REDACT-001), local OCR/embeddings, retention rule (PR-RETENTION-001, proposal);
  d1_m10 *Personal Data Standards* — Law 2024/017 in the KB (`CM-DP-2024`, 80 chunks), Malabo/ECOWAS comparison; d1_m11 *Data Governance* — `kb/raw/MANIFEST.json`, licence per source;
  d1_m12 *Standards-Compliant Formats* — RDF/SHACL/PROV-O ontology (`app/kg/ontology`, `docs/ontology.md`), JSONL KB; d1_m04 *Diligence Metrics* — licence and redistribution flag per source; d1_m08 *Fairness and Bias* — name normalisation for compound names and both name orders.
* **Not addressed.** d1_m02 model properties, d1_m05/m07 marketplace activity and participants, d1_m13 open-source models: there is no marketplace. d1_m06 *Open Datasets*: the KB and the synthetic benchmark are open **in the repository**, but nothing is published/measured yet.
* **Gaps surfaced:** S1 (no national diploma/PV schema), S4 (no OCR benchmark), P3 (retention), P5 (consent for employer checks).

### 2. Generated Content Marketplace — *Minimal*
* **Addressed.** d2_m02 *Guardrails* — the only generated text is explanations: templates by default (`LLM_PROVIDER=none`), optional local LLM; PR-NOACCUSE-001 blocks accusatory wording; d2_m05 *Regional Customization* — FR/EN bilingual templates.
* **Weak/not addressed.** d2_m04 *Fake Detection*: check C8 (image integrity hints) is optional, low weight, never decisive — it is not a deepfake/forgery detector. d2_m01/m03: no content marketplace, no multimodal generation.
* **Gap:** S8 (no vocabulary for verification outcomes / non-accusatory wording).

### 3. Cross-Domain Correlation Analysis — *Partial*
* **Addressed.** d3_m01 *Integrated Workflow* — prevention (consent/purpose/bulk rules), detection (checks C1–C8), response (registrar approval, holder notice), impact analysis (gap register, A/B diff);
  d3_m03 *Domain Models* — ontology + SHACL shapes; d3_m04 *Domain KPIs* — time-to-verdict, share of verdicts escalated to humans, evidence items per verdict (intended KPIs; produced by the scenario runner / `docs/ab_results.md` once `make demo` has run).
* **Not demonstrated.** d3_m06 *Cycle Time Reduction*: months -> seconds is a design claim from the problem statement (and the press-reported 1–3 year postal exchanges, `CM-VERIF-PLATFORM-2024`); only synthetic timings are measured. d3_m02/m05/m07 coverage and scale: not applicable.
* **Gaps:** P2 (probative value), P7 (national platform access terms).

### 4. Contextualization and Regional Impact — *Partial*
* **Addressed.** d4_m02 *Local Datasets* — synthetic Cameroonian-style names, francophone/anglophone institutions (`data_gen/names.py`, `data_gen/registry.py`); d4_m04 *Regional Customizations* — Cameroon legal KB (data protection, archives, penal, e-commerce, higher education 2001), MINESUP form; d4_m07 *Cultural Diversity* — compound names, surname/given-name order variants; d4_m09 *Sub-group Representation* — mix of francophone/anglophone and private/public institutions.
* **Not addressed.** d4_m01 regional innovation, d4_m03 users of indigenous services, d4_m05/m08 adoption elsewhere, d4_m06 knowledge products (the gap register and KB are the nearest candidates); no real local dataset is used (rule: synthetic only).
* **Gaps:** P8 (2023 higher-education law text unavailable), P11 (name reconciliation), S4.

### 5. Level of Integration of AI in Workflows — *Partial*
* **Addressed.** d5_m01 *Automation Level* — automated evidence retrieval and field checks with a mandatory human gate for adverse verdicts; d5_m07 *Integration Level* — Y.3172-style pipeline from SRC to SINK with the same code in sandbox/production profiles (`docs/y3172_mapping.md`); d5_m08 *Model Suitability* — rule-first, LLM optional.
* **Not measured.** d5_m02/m03/m04/m10/m12 (benefits, time/energy saved, efficiency, quality, cost): no baseline on real workflows.
* **Gap:** S3 (Y.3172 lacks the gates needed for this integration).

### 6. Human Interface — *Partial*
* **Addressed.** d6_m06 *Safety Integration* — no-accusation wording, uncertainty surfaced as `unknown` -> human review, evidence crops with highlights; d6_m04 *Usability Level* — Streamlit console, verdict badges, rule -> KB-text drill-down (UI pages in progress at time of writing); d6_m05 *Cultural Sensitivity* — neutral wording toward the holder.
* **Not addressed.** d6_m02 *Local Language Models*: French and English only; Cameroonian national languages are not supported (SNIA, as reported, plans multilingual models). d6_m03 channels: web UI only.
* **Gap:** P1 (what "human intervention" means operationally).

### 7. Strategy Alignment — *Partial*
* **Addressed.** d7_m03 *Intent Representations* — verification request + consent token + purpose, scenario YAMLs; d7_m04 *Interoperability Standards* — RDF/SHACL/PROV-O, W3C VC 2.0 alignment proposed; d7_m08 *International Cooperation* — cross-border rules (PR-CROSSBORDER-001/002, scenario S4) and UAE comparison; alignment with SNIA pillars (governance, data/interoperability, multilingual, education/justice use cases — as reported) and the AU AI Strategy / AU Data Policy Framework (KB sources).
* **Not addressed.** d7_m01 *Coordination Level* with MINESUP/MINFOPRA platform: no integration (terms unpublished, **P7**); d7_m02 time to go-live; d7_m05/m06/m07.
* **Gaps:** P4, P7, P10, S7.

### 8. Collaboration with AI — *Strong on human-in-the-loop, otherwise Minimal*
* **Addressed.** d8_m01 *Co-creation Ease (human in inference process)* — registrar approves/rejects every INCONSISTENT verdict (PR-REGISTRAR-001), uncertain results escalate (PR-HUMAN-REVIEW-001), approvals need a justification (PR-APPROVAL-AUDIT-001, scenario S7); d8_m05 *Baseline Comparison* — Version A (AI-only, ungoverned) vs Version B (AI + policy + human) on identical scenarios.
* **Not addressed.** d8_m02 traditional knowledge, d8_m04 prompting efficiency, d8_m06 incident action plans, d8_m07 fine-tuning effort; human-only baseline not measured.
* **Gaps:** P1, P6, **S6** (Readiness 2.0 has no metric for human-gate coverage or multi-agent coordination).

### 9. Impacts of Humans in AI Integration — *Minimal*
* **Addressed (indirectly).** d9_m04 *AI Awareness* — bilingual explanations tell registrars/verifiers why a verdict was reached; the registrar console doubles as training for reading evidence packs.
* **Not addressed.** d9_m01/m03 skills and ecosystem, d9_m05 training programmes, d9_m06 experts trained, d9_m07 skills gap, d9_m08 inference-to-action latency (the human step is the bottleneck by design; not measured). The higher-education texts in the KB (2001 law, Addis Convention) frame who trains and recognises qualifications but no programme is proposed.

### 10. AI & Policies — **Strong** (the project's centre of gravity)
* **Addressed.** d10_m01 *Policy Sandbox* — Version A/B scenario runner on synthetic data; d10_m02 *Policy Evaluation* — 11 required + 5 additional rules evaluated at five checkpoints with expected outcomes in `kb/policy_rules/examples.yaml`; d10_m04 *Horizontal Policies* — open KB of 31 catalogued sources / 964 chunks (26 sources with text); d10_m06 *Safety Policies* — consent, purpose, bulk limit, no-accusation, registrar gate; d10_m07 *Explainability Metrics* — every fired rule links to a real chunk, every verdict to evidence items; d10_m08 *Policy Case Briefs* — gap register `kb/gaps/gap_register.md` and `docs/gaps.md`; d10_m09 *Audit Metrics* — hash-chained append-only `audit_events`; d10_m10 *Data Sovereignty* — local-by-default processing, cloud LLM opt-in with transfer warning, cross-border minimal attestation.
* **Partial.** d10_m03 *Policy Extrapolation* — A/B simulation compares behaviours but there is no timeline simulation of policy interventions; d10_m05 community benefit: not measured.
* **Honesty markers.** Of 16 rules, 8 are `kind: proposal` (`proposal: true`: our own, not law); the rest are legal interpretations of cited texts. Law 2024/017 was read via OCR of the Presidency's certified copy.
* **Gaps:** P1–P12, S2, S3, S6 (see `docs/gaps.md`).

### 11. AI for Inclusion — *Partial*
* **Addressed.** d11_m06 *Edge Privacy* — standalone/offline operation with local OCR and embeddings; d11_m08 *Regional Language Support (offline)* — offline demo, FR/EN; d11_m01 *Digital Twins/sandbox environments* — synthetic institutions and actors; d11_m10 *Multi-modal Data* — scans, PDFs, CSV/XLSX, images with crops; d11_m13 *Social Inclusion Impact* — holders get notice, contest and rectification paths (PR-HOLDER-NOTICE-001), reducing the risk that name-variant or archive gaps exclude people (scenarios S5, S6).
* **Not addressed.** d11_m07 language repository for national languages and dialects, sign language support, d11_m04/m05 edge hardware/5G, d11_m12 women/indigenous leadership, d11_m09 avatars. No usability test with real users.
* **Gaps:** P11, P5 (consent), S4 (OCR benchmark for local administrative scans).

### 12. Granular Priorities — *Minimal*
* **Addressed.** d12_m01 *Regional Priorities* — diploma-fraud and slow verification are the chosen priority (press-reported figures in `CM-VERIF-PLATFORM-2024`, as reported); d12_m04 *Customized Workflows* — domain can be swapped by `KB_DIR` + ontology (e.g. civil-status certificates), see `kb/README.md`.
* **Not addressed.** d12_m02 fine-tuned models (none), d12_m03 organisational structure for evolving priorities.

### 13. Digital Infrastructure — *Partial*
* **Addressed.** d13_m01 *AI-Enabled Devices (Y.3172 nodes)* — explicit node mapping in `docs/y3172_mapping.md`; d13_m02 *Data Centers and APIs* — containerised FastAPI + Postgres/pgvector + Oxigraph (`docker-compose.yml`); d13_m04 *Edge Infrastructure* — CPU-only, runs offline on a laptop (no edge cloud); d13_m03 digital services — API + UI.
* **Not measured/addressed.** d13_m05/m06 energy, d13_m07 uptime, d13_m08 area coverage, d13_m09 network quality: no measurements. Cameroon's national infrastructure is not assessed here (SNIA, as reported, lists digital infrastructure as a pillar).

## Where the framework itself is thin (feeds standards gap S6)
Readiness 2.0 has related metrics — d8_m01 (human in inference process), d10_m07 (explainability metrics), d10_m09 (audit metrics), d6_m06 (safety integration), d3_m01 (integrated workflow) — but nothing that measures
(1) coordination of several agents under a policy guardian, (2) the proportion of adverse outputs that were human-approved before release, (3) evidence/provenance integrity, (4) the ratio of rules backed by binding law vs
proposals. We propose these four metrics (human-gate coverage, audit-chain verifiability, evidence-linked-output ratio, proposal-vs-law ratio) in `kb/gaps/seed_gaps.yaml` (S6).
