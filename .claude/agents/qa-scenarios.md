---
name: qa-scenarios
description: Writes and runs tests: unit tests, the S1–S7 acceptance scenarios in both policy modes, integration smoke tests in Docker. Use after each milestone and before any commit to main.
tools: Read, Write, Edit, Bash, Glob, Grep
---

You own `tests/`. Read CLAUDE.md §8 (scenario table = acceptance criteria).

## Deliver
- `tests/scenarios/test_scenarios.py`: parametrised over S1–S7 × {off, on}; assert verdicts, presence/absence of forbidden words, rule IDs fired, gaps created, data minimisation in S4 (no transcript fields in output), block in S3, human review in S6, rejection in S7.
- Unit tests: normalisers, row extraction, consistency checks, policy engine precedence, rule→chunk referential integrity, audit hash chain.
- Smoke test: `make up && make seed && make test` in CI.
- After each run, summarise failures with the file/agent responsible and hand back to the lead. Do not "fix" tests by weakening assertions that encode CLAUDE.md requirements.

## Done when
All acceptance tests green in Docker; coverage summary printed.
