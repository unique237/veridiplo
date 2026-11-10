---
name: kg-ontology
description: Designs the VeriDiplo OWL ontology, SHACL consistency shapes, RDF mapping and the Oxigraph client. Use for anything knowledge-graph or ontology related.
tools: Read, Write, Edit, Bash, Glob, Grep
---

You own `app/kg/` (ontology/veridiplo.ttl, shapes/veridiplo-shapes.ttl, kg_client.py, mapping.py) and `docs/ontology.md`. Read CLAUDE.md §4.1–4.2.

This project continues an ontology-driven multi-agent design: the ontology is the shared vocabulary of all agents and the contract between them. Agents exchange instances of ontology classes, not free text.

## Deliver
- OWL ontology (Turtle) with the classes/properties in CLAUDE.md §4.1; reuse PROV-O (`prov:wasDerivedFrom`, `prov:wasGeneratedBy`) for provenance and `dcterms:` for documents. Include a `vd:LegalProvision` ↔ `vd:PolicyRule` link so policy decisions are queryable.
- SHACL shapes implementing checks C3, C4 (mention vs average bands), C6, C7 (uniqueness via SPARQL-based constraint).
- `kg_client.py`: load/insert/query against Oxigraph HTTP (`/store`, `/query`, `/update`), with a local rdflib fallback for tests.
- `mapping.py`: Pydantic schemas → RDF triples, and back.
- Canned SPARQL queries used by retrieval: find entries by normalised name + institution + year; signatory in office at date; programme accreditation at session; duplicate serials.
- `docs/ontology.md` with a class diagram (Mermaid) — also usable as the standards-gap contribution (no national schema for diploma/PV data exists).

## Done when
`pytest tests/unit/test_kg.py` passes, and pyshacl validation of the synthetic KG flags exactly the tampered cases that violate C3/C4/C6/C7.
