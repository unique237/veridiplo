"""`make demo`: run S1-S7 in Version A (policy off) and Version B (policy on), write docs/ab_results.md.

Exit code 1 if any expectation of data/scenarios/S*.yaml fails. Usage: python -m scripts.run_demo [S1 S2 ...]
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from app.agents.gap_analyst import seed_gaps
from app.agents.scenario_runner import list_scenarios, run_scenario
from app.core.db import SessionLocal, init_db
from app.core.config import get_settings


def _short(s: object, n: int = 160) -> str:
    return str(s or "").replace("\n", " ").replace("|", "/")[:n]


def render(results: list[dict]) -> str:
    L: list[str] = ["# VeriDiplo - A/B results (Version A ungoverned vs Version B policy-governed)", "",
                    f"_Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} by `make demo` - "
                    "SPECIMEN - SYNTHETIC DATA. LLM_PROVIDER=" + get_settings().LLM_PROVIDER + "._", "",
                    "## Summary", "", "| Scenario | Version A (off) | Version B (on) | Expectations A | Expectations B |",
                    "|---|---|---|---|---|"]
    for r in results:
        sa, sb = {d["field"]: d for d in r["diff"]}, None
        d = {x["field"]: x for x in r["diff"]}

        def cell(side: str) -> str:
            v = d["verdict"][side]
            return f"{v or d['output_scope'][side] or '-'} / {d['status'][side]}"
        ea, eb = r["assertions"]["A"], r["assertions"]["B"]
        L.append(f"| **{r['scenario']['id']}** {_short(r['scenario']['title_en'], 50)} | {cell('A')} | {cell('B')} | "
                 f"{'PASS' if all(x['ok'] for x in ea) else 'FAIL'} ({sum(x['ok'] for x in ea)}/{len(ea)}) | "
                 f"{'PASS' if all(x['ok'] for x in eb) else 'FAIL'} ({sum(x['ok'] for x in eb)}/{len(eb)}) |")
    L.append("")
    for r in results:
        sc = r["scenario"]
        L += [f"## {sc['id']} - {sc['title_en']}", "", f"_{sc['title_fr']}_", "", _short(sc["description"], 700), "",
              "| | Version A (off) | Version B (on) | differs |", "|---|---|---|---|"]
        for row in r["diff"]:
            if row["field"] == "text":
                continue
            L.append(f"| {row['field']} | {_short(row['A'], 90)} | {_short(row['B'], 90)} | {'yes' if row['differs'] else ''} |")
        L += ["", "**Key quotes**", "",
              f"- A: > {_short(r['A']['result'].get('explanation_en') or r['A']['result'].get('explanation_fr'), 300)}",
              f"- B: > {_short(r['B']['result'].get('explanation_en') or r['B']['result'].get('explanation_fr'), 300)}"]
        msgs = [f"{f['rule_id']} ({f['action']}): {f['message_en']}" for f in r["B"]["result"].get("fired_rules", [])
                if f["action"] != "allow"]
        for m in msgs[:4]:
            L.append(f"- B rule {_short(m, 260)}")
        L.append("")
        L += ["**Expectations** (from `data/scenarios/%s.yaml`)" % sc["id"], "", "| Mode | Check | Result | Detail |", "|---|---|---|---|"]
        for mode in ("A", "B"):
            for a in r["assertions"][mode]:
                if a["name"].startswith("unchecked"):
                    continue
                L.append(f"| {mode} | {a['name']} | {'PASS' if a['ok'] else '**FAIL**'} | {_short(a['detail'], 100) if not a['ok'] else ''} |")
        L += ["", f"Audit chain: A {'valid' if r['A']['audit_chain_valid'] else 'BROKEN'} ({r['A']['audit_n']} events), "
              f"B {'valid' if r['B']['audit_chain_valid'] else 'BROKEN'} ({r['B']['audit_n']} events). "
              f"Cases: A `{r['A']['case_id']}`, B `{r['B']['case_id']}`.", ""]
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    init_db()
    with SessionLocal() as s:
        seed_gaps(s)
        s.commit()
    wanted = [a.upper() for a in argv] or [x["id"] for x in list_scenarios()]
    results, failed = [], []
    for sid in wanted:
        with SessionLocal() as s:
            r = run_scenario(s, sid)
            s.commit()
        results.append(r)
        bad = [f"{m}:{a['name']} ({a['detail']})" for m in ("A", "B") for a in r["assertions"][m] if not a["ok"]]
        print(f"{sid}: A={r['A']['status']} B={r['B']['status']} -> {'PASS' if not bad else 'FAIL'}")
        for b in bad:
            print("   x", b)
        if bad:
            failed.append(sid)
    out = Path(get_settings().DATA_DIR).parent / "docs" / "ab_results.md"
    if not out.parent.exists():
        out = Path("docs/ab_results.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not argv:
        out.write_text(render(results), encoding="utf-8")
        print(f"wrote {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
