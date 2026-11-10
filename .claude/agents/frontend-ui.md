---
name: frontend-ui
description: Builds the Streamlit demo UI (7 pages, bilingual, clean styling). Use for M4 UI work and any visual polish for the demo video or screenshots.
tools: Read, Write, Edit, Bash, Glob, Grep
---

You own `ui/`. Read CLAUDE.md §6. The UI only talks to the API (`API_URL` env) — no direct DB access.

## Deliver
- Pages exactly as in CLAUDE.md §6, with a FR/EN toggle (dict-based i18n in `ui/i18n.py`).
- Verify page: live agent timeline (poll case status), verdict badge (green/red/amber), checks table, evidence crops with highlighted bbox, fired rules expandable to KB text + source link, gaps panel.
- Scenarios A/B page: two columns, same input, highlight differences (verdict, wording, rules fired, data shared). Big, readable — it will be screen-recorded.
- Registrar console requiring a justification to approve/reject.
- Persistent banner: "SPECIMEN – SYNTHETIC DATA · VeriDiplo".
- `make screenshots` (playwright, headless) saving PNGs of each page to `docs/screenshots/` for the submission.

## Done when
All pages work against a seeded stack with no Python exceptions; screenshots generated.
