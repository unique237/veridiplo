"""Page 6 - Audit trail per case (hash chain verification) and gap register with Markdown export."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import (AMBER, GREEN, RED, STRETCH, api, badge, chip, esc, setup_page, short_id, warn_api)  # noqa: E402
from i18n import t  # noqa: E402

setup_page("page.audit.title", "page.audit.sub")
tab_audit, tab_gaps = st.tabs([t("audit.tab_audit"), t("audit.tab_gaps")])


def kpi(label: str, value: str) -> str:
    return f'<div class="vd-kpi"><div class="n">{esc(value)}</div><div class="l">{esc(label)}</div></div>'


# ------------------------------------------------------------------ audit
with tab_audit:
    v = api("GET", "/audit/verify", timeout=20)
    if v.ok and isinstance(v.data, dict):
        d = v.data
        valid = bool(d.get("chain_valid"))
        c1, c2, c3 = st.columns(3)
        c1.markdown(kpi(t("audit.global_chain"), ("✔ " + t("audit.valid")) if valid else ("✘ " + t("audit.broken"))),
                    unsafe_allow_html=True)
        c2.markdown(kpi(t("audit.events_total"), str(d.get("events_total", "?"))), unsafe_allow_html=True)
        c3.markdown(kpi(t("audit.head_hash"), str(d.get("head_hash") or "—")[:16]), unsafe_allow_html=True)
        if not valid:
            st.error(t("audit.broken_detail", id=d.get("first_bad_id")))
    else:
        warn_api(v, t("audit.tab_audit"))

    lst = api("GET", "/cases", params={"limit": 50}, timeout=15)
    cases = lst.data if lst.ok and isinstance(lst.data, list) else []
    options = [c["case_id"] for c in cases]
    manual = st.text_input(t("audit.case_id"), key="audit_manual", placeholder="uuid")
    sel = manual.strip() or st.selectbox(
        t("audit.pick"), options or [""],
        format_func=lambda cid: next((f'{short_id(c["case_id"])} · {c.get("holder") or "?"} · {c.get("verdict") or c.get("status")}'
                                      f'{" · " + c["scenario_id"] if c.get("scenario_id") else ""} · {c.get("policy_mode") or ""}'
                                      for c in cases if c["case_id"] == cid), t("audit.none")))
    if sel:
        r = api("GET", f"/audit/{sel}", timeout=20)
        if not r.ok or not isinstance(r.data, dict):
            warn_api(r, t("audit.tab_audit"))
        else:
            d = r.data
            ok = bool(d.get("chain_valid"))
            st.markdown((badge("✔ " + t("audit.chain_ok"), GREEN) if ok else badge("✘ " + t("audit.chain_ko"), RED)) +
                        chip(f'{d.get("count", len(d.get("events", [])))} {t("audit.events")}'), unsafe_allow_html=True)
            evs = d.get("events") or []
            df = pd.DataFrame([{
                "#": e.get("id"), "ts": str(e.get("ts"))[:19].replace("T", " "), "agent": e.get("agent"), "action": e.get("action"),
                "actor": e.get("actor"), "mode": e.get("policy_mode"), "rules": ", ".join(e.get("policy_rule_ids") or []),
                "kb": ", ".join(e.get("kb_chunk_ids") or []), "row_hash": str(e.get("row_hash") or "")[:12],
                "prev_hash": str(e.get("prev_hash") or "")[:12]} for e in evs])
            st.dataframe(df, hide_index=True, height=min(620, 60 + 36 * max(len(df), 1)), **STRETCH)
            with st.expander(t("audit.raw")):
                st.json(evs, expanded=False)
    else:
        st.info(t("audit.none"))

# ------------------------------------------------------------------ gaps
with tab_gaps:
    r = api("GET", "/gaps", timeout=20)
    gaps = (r.data or {}).get("gaps", []) if r.ok and isinstance(r.data, dict) else []
    if not r.ok:
        warn_api(r, t("audit.tab_gaps"))
    seeds = [g for g in gaps if not g.get("case_id")]
    runtime = [g for g in gaps if g.get("case_id")]
    c1, c2, c3 = st.columns([1, 1, 1])
    c1.markdown(kpi(t("gaps.register"), str(len(seeds))), unsafe_allow_html=True)
    c2.markdown(kpi(t("gaps.runtime"), str(len(runtime))), unsafe_allow_html=True)
    exp = api("GET", "/gaps/export.md", timeout=30)
    if exp.ok and exp.content:
        c3.download_button(t("gaps.export"), exp.content, file_name="veridiplo-gap-register.md", mime="text/markdown", **STRETCH)
    else:
        c3.button(t("gaps.export_na"), disabled=True, **STRETCH)

    st.subheader(t("gaps.register"))
    for g in seeds:
        d = g.get("detail") or {}
        with st.expander(f'{g.get("gap_id")} · {g.get("title")}'):
            st.markdown(badge(str(g.get("gap_id")), "#7a3db8") + chip(str(g.get("type"))), unsafe_allow_html=True)
            for key in ("finding", "proposal", "comparison"):
                if d.get(key):
                    st.markdown(f'**{esc(t("gaps." + key))}** — {esc(d[key])}', unsafe_allow_html=True)
            if d.get("surfaced_by"):
                st.caption(t("gaps.surfaced_by") + ": " + ", ".join(d["surfaced_by"]))
            if d.get("sources"):
                st.caption(t("gaps.sources") + ": " + ", ".join(d["sources"]))
    st.subheader(t("gaps.runtime"))
    if runtime:
        df = pd.DataFrame([{"gap": g.get("gap_id"), "case": short_id(g.get("case_id")), "scenario": g.get("scenario_id") or "",
                            "checkpoint": g.get("checkpoint") or "", "kind": (g.get("detail") or {}).get("kind", ""),
                            "reason": str((g.get("detail") or {}).get("reason", g.get("title")))[:160]} for g in runtime])
        st.dataframe(df, hide_index=True, **STRETCH)
    else:
        st.caption(t("gaps.none"))
