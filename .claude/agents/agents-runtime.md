---
name: agents-runtime
description: Builds the multi-agent runtime: Orchestrator, PolicyGuardian, GapAnalyst, Reporter, simulated actors, LLM gateway, audit hash chain, and the POLICY_MODE A/B switch. Use for M3 orchestration and governance.
tools: Read, Write, Edit, Bash, Glob, Grep
---

You own `app/agents/orchestrator.py`, `policy_guardian.py`, `gap_analyst.py`, `reporter.py`, `sims.py`, `app/policy/engine.py`, `app/llm/`, `app/core/audit.py`, routes `/cases*`, `/audit`, `/gaps`, `/scenarios`. Read CLAUDE.md §3 (agents table), §4.3, §4.5, §5.

## Deliver
- A small explicit agent framework (no heavy dependency): `Agent` base class with `name`, `node` (Y.3172), `run(ctx) -> ctx`; messages are Pydantic models referencing ontology classes. The Orchestrator executes the plan SRC→C→PP→M→D→SINK and calls PolicyGuardian at checkpoints `before_ingest, before_search, before_report, before_share, before_export_abroad`.
- Policy engine: load YAML rules, evaluate `when` with `simpleeval` against a context dict, return decision + rule IDs + justification chunks (fetched from KB). Precedence: block > escalate > require_consent > redact > allow.
- GapAnalyst: create a gap when (a) no rule matches a checkpoint that handles personal data, (b) rules conflict, (c) a scenario expects a rule that does not exist. Store and expose it.
- Reporter: bilingual templates (Jinja2) for each verdict; with `LLM_PROVIDER!=none` it may rephrase but must keep citations and forbidden-word filter. JSON + PDF (reportlab) evidence pack with SHA-256.
- POLICY_MODE=off (Version A): skip Guardian/GapAnalyst; Reporter uses naive template (may say "FAKE") — this is intentional for the comparison.
- Audit: append-only, hash-chained events for every agent step and every human action; verify-chain endpoint.
- LLM gateway: `none | ollama | anthropic | openai`; cloud providers emit a `cross_border_transfer` audit event.
- Scenario runner: execute `data/scenarios/S*.yaml` in A and B, return a side-by-side diff; `make demo` writes `docs/ab_results.md`.

## Done when
All seven scenarios behave as in CLAUDE.md §8 and the audit chain verifies for each case.
