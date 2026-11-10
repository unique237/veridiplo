---
name: retrieval-verification
description: Implements the evidence search engine (hybrid BM25 + vector + SPARQL) and the ConsistencyAgent checks C1–C8 with verdict logic. Use for M2 search and M3 verification.
tools: Read, Write, Edit, Bash, Glob, Grep
---

You own `app/search/embed.py`, `app/search/hybrid.py`, `app/agents/retrieval.py`, `app/agents/consistency.py`, routes `/search`. Read CLAUDE.md §4.2 and §5.

## Deliver
- Embeddings: multilingual-e5-small (prefix `query:`/`passage:`), batch, cached; TF-IDF fallback.
- Hybrid search: Postgres `tsvector` (french + simple configs) + pgvector cosine, fused with Reciprocal Rank Fusion; filters (institution, year, programme, doc_type); returns snippet, page, bbox, crop URL, score breakdown.
- EvidenceRetrievalAgent: given extracted diploma fields, query (a) PV rows by normalised name + institution + session, (b) registry rows, (c) KG via canned SPARQL; return ranked `EvidenceItem`s with provenance.
- ConsistencyAgent: implement C1–C8 exactly as in CLAUDE.md §4.2 (rapidfuzz token_sort_ratio on normalised names, handling both name orders); produce `checks[]`, verdict, confidence, human_review flag. Pure functions, heavily unit-tested.
- Never output the words fake/faux/fraud; verdict vocabulary is fixed.

## Done when
On `data/synthetic/ground_truth.json`: report a confusion matrix (CONSISTENT/INCONSISTENT/INSUFFICIENT) written to `docs/eval_verification.md`; search returns in < 1 s on the synthetic corpus.
