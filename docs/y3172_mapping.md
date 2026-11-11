# ITU-T Y.3172 mapping — VeriDiplo pipeline, agents, policy rules, KB and standards gaps

> **Honest framing.** ITU-T Y.3172 (06/2019) is an *architectural framework for machine learning in future networks including IMT-2020*.
> It describes ML pipelines inside telecommunication networks. VeriDiplo applies its **vocabulary and pipeline pattern** (SRC, C, PP, M, P, D, SINK, MLFO, ML Intent,
> ML sandbox) to a different domain — documentary evidence for diploma verification. That transfer is **our adaptation**, not something the Recommendation
> prescribes; where the fit is poor we say so (section 4) and turn the misfit into standards gaps (section 5).
>
> Source of every Y.3172 statement below: the official PDF downloaded from itu.int (free copy), chunked in `kb/chunks/ITU-Y3172.jsonl`
> (clause numbers are quoted; chunk ids given). © ITU 2019 — short excerpts only.
> Implementation status is that of the repository on 11 Nov 2026 (files listed exist unless marked *planned*).

## 1. What Y.3172 defines (verified in the KB)

| Y.3172 element | Clause / chunk | Statement (short) |
|---|---|---|
| ML pipeline | 3.2.5, 8.1 (`ITU-Y3172#c004`, `#c020`) | "a set of logical nodes, each with specific functionalities, that can be combined to form a machine learning application" |
| SRC | 8.1 | source of data used as input to the pipeline |
| C (collector) | 8.1 | collects data from one or more SRC nodes (may configure them) |
| PP (preprocessor) | 8.1 | "cleaning data, aggregating data or performing any other preprocessing" so the model can consume it |
| M (model) | 8.1 | the ML model, in a form usable in a pipeline |
| **P (policy)** | 8.1 (`ITU-Y3172#policy_node` -> `#c020`) | "enables the application of policies **to the output of the model node**" (NOTE 5: e.g. night-time upgrades to protect network sanity) |
| D (distributor) | 8.1 | identifies the SINK(s) and distributes the output of M |
| SINK | 8.1 | target of the ML output "on which it takes action" |
| MLFO | 3.2.2, 8.1 (`ITU-Y3172#mlfo` -> `#c004`) | node that manages and orchestrates pipeline nodes, selects/reselects the model, places and chains nodes, driven by **ML Intent** |
| ML Intent | 8.1 NOTE 12 | declarative description of an ML application, technology-agnostic |
| ML sandbox | 3.2.6, 8.1 (`ITU-Y3172#sandbox` -> `#c021`) | isolated domain to train, test and evaluate pipelines before deployment on a live network |
| Three subsystems | 8.2 (`#c023`-`#c025`) | management (MLFO), ML pipeline, ML sandbox; reference points 1-9 |
| Requirements | clause 7 (`#c008`-`#c018`) | REQ-ML-COR-001..003, DEP-001..004, INT-001..004, SPEC-001..004, MNG-001..005 |
| Security | clause 9 (`ITU-Y3172#security` -> `#c027`) | general network security applies; "required to prevent unauthorized access to, and data leaking from, an ML pipeline" |

## 2. Overall mapping (diagram)

```
Intent (request + consent token) ─► Orchestrator (MLFO)         [ML Intent -> case request: purpose, requester, consent]
   SRC ─► C ───────► PP ─────────────► M ───────────────────► D ──────► SINK
   PV scans  intake   OCR, layout,      evidence retrieval +   reporter   registrar console,
   registries hash,   field extraction  consistency (C1-C8)    (pack,     verifier dashboard,
   diploma   prov.    -> RDF + vectors  + SHACL                hash FR/EN) holder notice
        ▲        ▲          ▲                 ▲                  ▲           ▲
        └────── Policy Guardian + Gap Analyst at 5 checkpoints (P) ───────────┘
   Sandbox profile = ML sandbox: synthetic data + Version A/B runner;   Production profile = same code
```

## 3. Node-by-node mapping

Legend: **Impl.** = implemented in the repository now; *planned* = specified in CLAUDE.md §3, not yet in the tree. Rule ids are those of `kb/policy_rules/*.yaml`
(`guards_node` field). KB anchors resolve through `kb/chunks/anchors.json`.

### SRC — sources of evidence
| Aspect | VeriDiplo |
|---|---|
| Components | scanned PV PDFs, result lists, registry CSV/XLSX, diploma scan/typed fields, **holder consent token**. All synthetic: `data_gen/` (generator, renderer, degradations, `data/scenarios/S1..S7.yaml`, `data/synthetic/`). Upload endpoint `POST /ingest` (API) |
| Agent | IntakeAgent (*planned* as a separate agent; ingestion is currently driven by `data_gen`/worker) |
| Policy rules | **PR-RETENTION-001** (guards SRC; `before_search` check that no upload older than 90 days is used; `proposal: true`). `before_ingest` has **no rule** by design of the current set: the GapAnalyst records it as a runtime gap (`CHECKPOINT_GAP = {before_ingest: P3}`) |
| KB sources | CM-DP-2024 Art. 9 (consent `#consent`), Art. 13/28 (retention `#retention`, `#retention_max`); CM-MINESUP-FORM (`#autorisation`); CM-ARCH-2024 (archives law); CM-ARCH-2000 (historic) |
| Y.3172 coverage | SRC = "source of data" (8.1). Y.3172 says nothing about lawfulness of the source, consent or retention -> gap **S3**, policy gaps **P3, P5, P9** |

### C — collector / intake
| Aspect | VeriDiplo |
|---|---|
| Components | file hashing (SHA-256, `app/core/hashing.py`), provenance metadata (PROV-O in `app/kg/ontology/veridiplo.ttl`), case creation (`app/core/models.py: Case`), request intake (consent token, purpose, requester) |
| Agent | IntakeAgent (*planned*); Orchestrator (*planned*) |
| Policy rules | PR-CONSENT-001/002/003, PR-PURPOSE-001, PR-BULK-001 at `before_search` (they guard the transition C -> M, `guards_node: M`) |
| KB sources | CM-DP-2024 Art. 9, 11, 27 (`#consent`, `#purpose`, `#security`: identity verification of third parties receiving data), INT-MALABO-2014 Art. 13 (`#consent`), ECOWAS-DPA-2010 Art. 23, W3C-PROV-O |
| Y.3172 coverage | C collects from SRC and "may configure SRC nodes" (8.1 NOTE 3). Nothing on provenance, hashing or chain of custody -> **S3**, **P2** |

### PP — preprocessing
| Aspect | VeriDiplo |
|---|---|
| Components | OCR `fra+eng` with page/row/bbox chunks (`app/agents/ocr.py`), name/date normalisation (`app/core/normalize.py`, `app/core/names.py`), RDF mapping (`app/kg/mapping.py`), embeddings (`app/search/embed.py`) |
| Agent | OCRAgent (impl.), ExtractionAgent (*planned*; regex+rules first) |
| Policy rules | none specific (data minimisation applies at D). OCR-confidence feeds PR-HUMAN-REVIEW-001 via `verdict.confidence` |
| KB sources | CM-ARCH-2024 Art. 2, 19, 29 (electronic archives, certified copies); W3C-PROV-O; W3C-SHACL |
| Y.3172 coverage | PP = cleaning/aggregation. No notion of preprocessing **confidence/uncertainty propagation** or evidence localisation (page/bbox) -> **S4** (OCR benchmark), **S1** (schema) |

### M — model: evidence retrieval + consistency
| Aspect | VeriDiplo |
|---|---|
| Components | hybrid search Postgres FTS + pgvector (`app/search/hybrid.py`), SPARQL over the KG (`app/kg/kg_client.py`), retrieval (`app/agents/retrieval.py`), checks C1-C8 + verdict logic + SHACL (`app/agents/consistency.py`, `app/kg/shapes/veridiplo-shapes.ttl`) |
| Agent | EvidenceRetrievalAgent, ConsistencyAgent (impl.) |
| Policy rules | PR-LLM-TRANSFER-001 (guards M: cloud LLM call = transfer abroad). The verdict itself is the output on which P acts |
| KB sources | CM-DP-2024 Art. 44 (`#automated_decision`), INT-MALABO-2014 Art. 14, EU-GDPR Art. 22, EU-AI-ACT Art. 14/86 (comparison), NIST AI RMF, OECD AI principles (summary) |
| Y.3172 coverage | M = "a machine learning model". **Our M is mostly deterministic** (fuzzy matching, rules, SHACL; LLM optional and off by default) — Y.3172's model-selection/training feedback (MLFO, REQ-ML-MNG-001..004) largely does not apply. Honest mismatch (section 4) |

### P — policy
| Aspect | VeriDiplo |
|---|---|
| Components | `app/policy/engine.py` (simpleeval evaluator), `app/policy/loader.py` (rules + KB integrity), `kb/policy_rules/*.yaml` (16 rules), `app/agents/policy_guardian.py`, `app/agents/gap_analyst.py`, audit hash chain (`app/core/audit.py`, table `audit_events`) |
| Agent | PolicyGuardianAgent, GapAnalystAgent (impl.). `POLICY_MODE=off` disables both (Version A) |
| Policy rules | all 16; five checkpoints `before_ingest/search/report/share/export_abroad`; actions `allow/redact/require_consent/block/escalate` |
| KB sources | every rule `justified_by` -> real chunk (enforced by `tests/unit/test_policy_rules.py`); 56 anchors; Y.3172 itself (`#policy_node`) |
| Y.3172 coverage | P "applies policies to the output of the model node", placed between M and D. **VeriDiplo extends P into a cross-cutting guardian**: it also decides about ingestion, search, release and export. This is the largest deviation and the core of standards gap **S3** |

### D — distributor / reporter
| Aspect | VeriDiplo |
|---|---|
| Components | evidence pack JSON/PDF with SHA-256 integrity hash, bilingual explanation, no-accusation templates (`app/agents/reporter.py`) |
| Agent | ReporterAgent (impl.) |
| Policy rules | **PR-REDACT-001** (minimise fields), **PR-NOACCUSE-001** (forbidden wording), **PR-EVIDENCE-001** (no verdict without cited evidence), **PR-HUMAN-REVIEW-001** (uncertain -> escalate), PR-CROSSBORDER-001/002 (`before_export_abroad`) |
| KB sources | CM-PEN-2016 Art. 144/219/314-315 (`#forgery_public_act`, `#title_usurpation`, `#private_forgery`), CM-ARCH-2024 Art. 29 (`#evidential_copy`), W3C-VC-2.0 s.8.9 (data minimisation), EU-AI-ACT Art. 13 |
| Y.3172 coverage | D identifies SINKs and distributes output (8.1). No requirement that the distributed output be **explainable, minimised or evidence-linked** -> **S3, S8** |

### SINK — consumers acting on the output
| Aspect | VeriDiplo |
|---|---|
| Components | registrar console (approve/reject + mandatory justification), verifier dashboard, holder notice (Streamlit `ui/`, API `/cases/{id}/review`) — pages *in progress* (`ui/pages/` empty at time of writing) |
| Agent | RegistrarSim/VerifierSim/HolderSim (*planned*; human can take over registrar role) |
| Policy rules | **PR-REGISTRAR-001** (INCONSISTENT held until approved), **PR-APPROVAL-AUDIT-001** (approval without >=20-char justification blocked + supervisor alert), **PR-HOLDER-NOTICE-001** (notify + contest/rectify path) |
| KB sources | CM-DP-2024 Art. 44(3) (`#human_intervention`: human intervention, point of view, contest), Art. 12 (`#accuracy`), Art. 62 (`#remedy`), Art. 29 (`#accountability_register`), EU-AI-ACT Art. 12/14 (`#record_keeping`, `#human_oversight`) |
| Y.3172 coverage | SINK "takes action" on the ML output (8.1 NOTE 7: a UE adjusting measurement periodicity). Y.3172 contains **no human decision-maker in the loop**; our SINK is a person affected by the result -> **S3**, policy gaps **P1, P6** |

### Management subsystem — MLFO / ML Intent
| Y.3172 | VeriDiplo |
|---|---|
| MLFO orchestrates and chains nodes, selects models | Orchestrator agent (*planned*): parses the request, builds the plan, calls agents in order, enforces Policy Guardian checkpoints, writes the audit trail. No model re-selection or placement (single-host deployment) |
| ML Intent (declarative, technology-agnostic) | the **verification request** (purpose, requester type/country, consent token, scope) and scenario YAMLs `data/scenarios/S*.yaml`; the policy context (`kb/policy_rules/context_schema.yaml`) is its machine-readable contract |

### ML sandbox subsystem
| Y.3172 | VeriDiplo |
|---|---|
| Isolated domain to test pipelines before live deployment | **Sandbox profile**: synthetic data only (SPECIMEN watermark), offline, `LLM_PROVIDER=none`; the **Version A / Version B scenario runner** is the sandbox experiment (same code in "production" profile). `make demo` -> `docs/ab_results.md` |
| Simulated ML underlay networks | simulated institutions, registries and actors (`data_gen/`, sims) |
| Gap | the sandbox has **no legal status** in Cameroon (no AI/sandbox regime found) -> **P12** |

## 4. Requirement-by-requirement (clause 7)

| Requirement | Applies? | How VeriDiplo responds |
|---|---|---|
| REQ-ML-COR-001 correlate data from multiple sources | yes | evidence from PV scans + registries + accreditation records are correlated by KG + SHACL + checks C1-C7 |
| REQ-ML-COR-002 interface with external non-IMT-2020 entities | partly | verifiers/ministries/embassies via API; interoperability formats are a gap (**S7**) |
| REQ-ML-COR-003 multi-level (UE/AN/CN) distribution | no | single-level deployment |
| REQ-ML-DEP-001..004 placement/flexibility/new sources | partly | containers, DB-backed worker; new archives ingested via `/ingest` (DEP-004 plug-and-play sources) |
| REQ-ML-INT-001..004 interfaces, model/data transfer across levels | partly | REST API, RDF/SPARQL, JSONL KB export; no cross-level model transfer |
| REQ-ML-SPEC-001..004 declarative specification, third parties | yes (adapted) | request/scenario YAML + policy YAML are declarative; third-party extension through the KB/rule format (**S2**) |
| REQ-ML-MNG-001 model selection | n/a | deterministic checks; optional LLM selected by env |
| REQ-ML-MNG-002..004 training, monitoring effect on the network, performance thresholds | adapted | monitoring = A/B scenario results and gap register; thresholds = `confidence < 0.6 -> human review` (our proposal). Y.3172 monitors the effect **on the network**, not on the **people** affected -> **S3** |
| REQ-ML-MNG-005 chaining | yes | Orchestrator chain SRC->C->PP->M->P->D->SINK |
| Clause 9 security | partly | local OCR/embeddings by default, cloud LLM opt-in with transfer warning (PR-LLM-TRANSFER-001), hash-chained append-only audit log; Y.3172 gives no privacy or audit requirement, so these come from Law 2024/017 and our rules |

## 5. What Y.3172 lacks -> standards gaps (also in `kb/gaps/gap_register.md`)

1. **P node scope (S3).** Defined only on the *output of M*. Diploma verification needs policy decisions before ingestion (consent, retention), before search (purpose, bulk limits), before release (wording, approval) and before export (transfer). *Proposal:* extend P with named checkpoints and an explicit action vocabulary (`allow/redact/require_consent/block/escalate`).
2. **No evidence/provenance function (S3).** No node or requirement ties an output to its source records (page, row, bounding box, hash). *Proposal:* a provenance requirement on C/PP and an evidence-linked output requirement on D, based on W3C PROV-O (and VC 2.0 `evidence`).
3. **No human-approval gate (S3).** SINK is an automated actor. *Proposal:* add a "human approval gate" sub-function of P for outputs with legal or employment effects, with mandatory justification and audit.
4. **No consent/purpose concept (S3, S5).** *Proposal:* a machine-readable consent artefact in the ML Intent.
5. **No explainability or minimisation requirement on D (S3, S8).**
6. **Minimal security clause.** Clause 9 asks for authentication/authorisation and leak prevention; it does not require audit logging, tamper-evidence or cross-border controls.
7. **Monitoring targets networks, not people.** REQ-ML-MNG-003 monitors KPIs of the network; there is no requirement to monitor false positives against affected individuals (**P6**).
8. **Policy-as-code format (S2)** and **no OCR/Africa-language benchmark (S4)** are outside Y.3172 but needed to make the pipeline reproducible.

## 6. Traceability: how a judge can check the mapping live

* (API per CLAUDE.md §5 — availability depends on integration status) `GET /kb/rules`, `GET /kb/search?q=` and UI page *Base de connaissances*: each rule shows `pipeline_node`, `guards_node`, `kind` (legal / legal_interpretation / proposal), `readiness_dims`, and the justifying chunks (clause/article text, URL, binding status).
* `audit_events` rows carry `agent`, `policy_rule_ids`, `kb_chunk_ids`, `policy_mode`; hash chain verified in UI page *Audit & écarts*.
* Pipeline page (*Pipeline Y.3172*): static diagram + live counters per node.
* `pytest tests/unit/test_policy_rules.py` fails if any rule cites a chunk that does not exist.
