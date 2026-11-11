# Evaluation of retrieval and consistency (40 synthetic diplomas)

All data is synthetic (SPECIMEN - SYNTHETIC DATA). `data/synthetic/ground_truth.json` and `manifest.json` are used
only as test oracles by `scripts/eval_verification.py`; application code never reads them.
Embedding backend during this run: **hashing** (deterministic hashing fallback, 384-d).

Reproduce: `docker compose exec -T api python -m scripts.eval_verification [--shacl]` (after `make seed`).

**Data source of this run:** database loaded from the clean text layer of the synthetic archive (PV rows from pv_index.json as rendered, registries parsed by app.agents.registry), hashing-fallback embeddings, no OCR errors. This is an upper bound for retrieval and consistency; re-run after `make seed` on the real OCR ingestion to measure the end-to-end effect of OCR noise.

## Decisions and thresholds

* C1: name similarity (rapidfuzz `token_sort_ratio` on normalised names, accent and order insensitive): >= 92 pass, 80-92 `unknown` + human review, < 80 `fail`
  only when candidates exist in the claimed institution/session; otherwise `unknown` (no comparable evidence).
  Guard added by the retrieval agent: a name that is below 80 by token-sort but >= 80 token by token (`token_align_sim`, e.g. one OCR slip on a first letter that changes the token order)
  is `unknown` + human review, never a `fail`. The guard can only lower a `fail`; it never produces a `pass`.
* Retrieval scopes PV rows to institution + programme + session, then widens to programme and institution when the name is not found; a name found outside the claimed session
  passes C1 and fails C5 (wrong session date).
* **C5 is treated as a critical check** (project decision): the wrong-session tampering is only visible through it.
* C3 is evaluated at the PV deliberation date when the ingestion stored it (`source_documents.meta.deliberation_date`), otherwise over the whole session year.
* C4 compares average (tolerance 0.01) and mention with the PV row (registry only if the PV value is unreadable) and checks the mention against the average band.
* C7: format regex from the knowledge graph, serial year = session year, and uniqueness (same serial held by a different holder in the registry).
* C8 (image hints) is optional, low weight and never decisive: it is `unknown` unless an OCR-confidence value is supplied.
* Verdict: any critical `fail` with evidence -> INCONSISTENT; no PV and no registry evidence -> INSUFFICIENT_EVIDENCE; critical `unknown` -> INSUFFICIENT_EVIDENCE + human review; all critical `pass` -> CONSISTENT.
* Confidence = weighted pass ratio (C1 3, C4 3, C3/C5/C6/C7 2, C2 1, C8 0.5) x mean OCR confidence of the PV/registry rows used. It measures how well the diploma agrees with the sources, so it is low for INCONSISTENT.
* Version A (`mode=off`, naive retrieval): no scoping, nearest name over the whole archive, no `unknown` band (similarity >= 92 passes, otherwise fails).
  The brief said 'pass at >= 85', but with that bar the OCR-hard names (88.9 and 87.3) would pass; 92 is used so that scenario S6 reproduces the false INCONSISTENT of Version A.

## Confusion matrix, Version B (policy-governed, `mode=on`)

| expected \ obtained | CONSISTENT | INCONSISTENT | INSUFFICIENT_EVIDENCE |
|---|---|---|---|
| **CONSISTENT** | 21 | 0 | 0 |
| **INCONSISTENT** | 0 | 15 | 0 |
| **INSUFFICIENT_EVIDENCE** | 0 | 0 | 4 |

Verdict accuracy: **40/40**. Failing-check sets exactly as expected: **40/40**. Expected `unknown` checks all raised: **40/40**.

## Confusion matrix, Version A (ungoverned, `mode=off`)

| expected \ obtained | CONSISTENT | INCONSISTENT | INSUFFICIENT_EVIDENCE |
|---|---|---|---|
| **CONSISTENT** | 21 | 0 | 0 |
| **INCONSISTENT** | 0 | 15 | 0 |
| **INSUFFICIENT_EVIDENCE** | 0 | 4 | 0 |

Verdict accuracy: **36/40** (the difference with Version B is the point of scenarios S5 and S6).

## Per diploma

| case | label | expected | obtained (B) | failing | unknown | conf | human | ok | obtained (A) |
|---|---|---|---|---|---|---|---|---|---|
| OCRH-001 | ocr_hard_name | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | - (exp. -) | C1,C2,C4,C5,C8 | 0.37 | yes | OK | INCONSISTENT |
| OCRH-002 | ocr_hard_name | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | - (exp. -) | C1,C2,C4,C5,C8 | 0.37 | yes | OK | INCONSISTENT |
| GEN-001 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-002 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-003 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-004 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-005 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-006 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-007 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-008 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-009 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-010 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-011 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-012 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-013 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-014 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-015 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-016 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-017 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-018 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| GEN-019 | genuine | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| REVN-001 | reversed_name_order | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| REVN-002 | reversed_name_order | CONSISTENT | CONSISTENT | - (exp. -) | C8 | 0.92 | no | OK | CONSISTENT |
| TMEN-001 | tampered_mention | INCONSISTENT | INCONSISTENT | C4 (exp. C4) | C8 | 0.73 | yes | OK | INCONSISTENT |
| TMEN-002 | tampered_mention | INCONSISTENT | INCONSISTENT | C4 (exp. C4) | C8 | 0.73 | yes | OK | INCONSISTENT |
| TMEN-003 | tampered_mention | INCONSISTENT | INCONSISTENT | C4 (exp. C4) | C8 | 0.73 | yes | OK | INCONSISTENT |
| TAVG-001 | tampered_average | INCONSISTENT | INCONSISTENT | C4 (exp. C4) | C8 | 0.73 | yes | OK | INCONSISTENT |
| TAVG-002 | tampered_average | INCONSISTENT | INCONSISTENT | C4 (exp. C4) | C8 | 0.73 | yes | OK | INCONSISTENT |
| TNAM-001 | tampered_name | INCONSISTENT | INCONSISTENT | C1 (exp. C1) | C2,C4,C5,C8 | 0.32 | yes | OK | INCONSISTENT |
| TNAM-002 | tampered_name | INCONSISTENT | INCONSISTENT | C1 (exp. C1) | C2,C4,C5,C8 | 0.32 | yes | OK | INCONSISTENT |
| WSES-001 | wrong_session_date | INCONSISTENT | INCONSISTENT | C5 (exp. C5) | C2,C8 | 0.69 | yes | OK | INCONSISTENT |
| WSES-002 | wrong_session_date | INCONSISTENT | INCONSISTENT | C5 (exp. C5) | C2,C8 | 0.69 | yes | OK | INCONSISTENT |
| NACC-001 | not_accredited | INCONSISTENT | INCONSISTENT | C3 (exp. C3) | C8 | 0.80 | yes | OK | INCONSISTENT |
| NACC-002 | not_accredited | INCONSISTENT | INCONSISTENT | C3 (exp. C3) | C8 | 0.80 | yes | OK | INCONSISTENT |
| SOOO-001 | signatory_out_of_office | INCONSISTENT | INCONSISTENT | C6 (exp. C6) | C8 | 0.80 | yes | OK | INCONSISTENT |
| SOOO-002 | signatory_out_of_office | INCONSISTENT | INCONSISTENT | C6 (exp. C6) | C8 | 0.80 | yes | OK | INCONSISTENT |
| DUPS-001 | duplicate_serial | INCONSISTENT | INCONSISTENT | C7 (exp. C7) | C8 | 0.80 | yes | OK | INCONSISTENT |
| DUPS-002 | duplicate_serial | INCONSISTENT | INCONSISTENT | C7 (exp. C7) | C8 | 0.80 | yes | OK | INCONSISTENT |
| MISS-001 | missing_archive | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | - (exp. -) | C1,C2,C4,C5,C8 | 0.34 | yes | OK | INCONSISTENT |
| MISS-002 | missing_archive | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | - (exp. -) | C1,C2,C4,C5,C8 | 0.34 | yes | OK | INCONSISTENT |

## SHACL cross-check (C2-C7)

The pure-python checks and the SHACL shapes (`app/kg/shapes`) agree on **38/40** diplomas (same set of failing structural checks). Disagreements:

* OCRH-001: SHACL ['C7'] vs python []
* OCRH-002: SHACL ['C7'] vs python []

Known, intended divergence: the SHACL uniqueness shape (C7) compares exact name keys, so a genuine holder whose name is written differently on the diploma (OCR-hard cases, similarity 80-92) looks like a second holder of the serial. The python check treats rows with similarity >= 80 as the same holder, which avoids a false INCONSISTENT; those cases end as INSUFFICIENT_EVIDENCE through C1 anyway.

## Sensitivity to OCR-like name errors (C1, Version B)

Seeded perturbation of the names of the genuine-looking diplomas (random character substitutions/deletions), 20 variants per name:

| errors per name | C1 pass (>= 92) | fail by token-sort alone | fail with alignment guard | unknown (human review) |
|---|---|---|---|---|
| 0 | 100 % | 0 % | 0 % | 0 % |
| 1 | 89 % | 8 % | 0 % | 11 % |
| 2 | 53 % | 17 % | 0 % | 47 % |

Real OCR errors are not uniform random: the numbers show the design limit of the 92 threshold on short names, not the accuracy of the OCR agent.


## Search

Name queries (limit 5, hit = a row of the same institution whose name has similarity >= 92 with the true holder name):

| query | recall@5 |
|---|---|
| diploma name | 36/38 (94.7 %) |
| true name | 38/38 (100.0 %) |

Recall@5 by diploma label (query = name as written on the diploma): duplicate_serial: 2/2, genuine: 19/19, not_accredited: 2/2, ocr_hard_name: 2/2, reversed_name_order: 2/2, signatory_out_of_office: 2/2, tampered_average: 2/2, tampered_mention: 3/3, tampered_name: 0/2, wrong_session_date: 2/2.

Latency of `search()` over the whole corpus (hybrid FTS + vector + trigram + RRF, warm): median 20 ms, p95 26 ms, max 28 ms over 76 queries; with institution + year filters: median 5 ms.

Retrieval + consistency per diploma (Version B): median 61 ms, max 266 ms.

Outputs containing the forbidden words: **0**.

## Open issues

* none on the 40 diplomas.
* Registry coverage is not verified for C7: a serial with a valid format and no registry row is reported `pass` with score 0.6 and a note.
* Hashing fallback vectors are lexical (character n-grams), not semantic; with multilingual-e5-small the vector channel adds semantic matches (the model must be in the HF cache volume).
