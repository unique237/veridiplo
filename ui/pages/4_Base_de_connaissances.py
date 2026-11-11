"""Page 4 - Knowledge base: search legal chunks, browse policy rules (with justification) and sources."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from common import AMBER, GREEN, GREY, api, badge, chip, esc, pick, setup_page, warn_api  # noqa: E402
from i18n import t  # noqa: E402
from render import ACTION_COLOR, chunk_badges, render_chunk  # noqa: E402

setup_page("page.kb.title", "page.kb.sub")
tab_search, tab_rules, tab_sources = st.tabs([t("kb.tab_search"), t("kb.tab_rules"), t("kb.tab_sources")])

# ------------------------------------------------------------------ chunk search
with tab_search:
    with st.form("kb_form"):
        c1, c2 = st.columns([4, 1])
        q = c1.text_input(t("kb.query"), value=st.session_state.get("kb_q", ""), placeholder=t("kb.placeholder"))
        binding = c2.selectbox(t("kb.binding_filter"), ["", "true", "false"],
                               format_func=lambda x: {"": t("search.any"), "true": t("kb.binding"), "false": t("kb.nonbinding")}[x])
        go = st.form_submit_button(t("search.go"), type="primary")
    if go and q.strip():
        st.session_state["kb_q"] = q
        params = {"q": q.strip(), "limit": 12}
        if binding:
            params["binding"] = binding
        r = api("GET", "/kb/search", params=params, timeout=20)
        if r.ok and isinstance(r.data, dict):
            st.session_state["kb_res"] = r.data
        else:
            warn_api(r, t("kb.tab_search"))
    elif go:
        st.warning(t("search.need_query"))
    data = st.session_state.get("kb_res")
    if data:
        results = data.get("results") or []
        st.markdown(f'**{len(results)}** {esc(t("search.results"))}')
        for c in results:
            with st.container(border=True):
                st.markdown(f'**{esc(c.get("chunk_id"))}** — {esc(c.get("title"))}<br>{chunk_badges(c)}', unsafe_allow_html=True)
                st.caption(f'{c.get("issuer") or ""} · {c.get("date") or ""} · {c.get("jurisdiction") or ""}'
                           + (f' · {t("search.score")} {c["score"]}' if c.get("score") is not None else ""))
                st.markdown(f'<div class="vd-text" style="font-size:.9rem">{esc(c.get("text"))}</div>', unsafe_allow_html=True)
                if c.get("url"):
                    st.markdown(f'[{t("kb.source_link")}]({c["url"]})')
    else:
        st.info(t("kb.empty"))

# ------------------------------------------------------------------ rules
with tab_rules:
    r = api("GET", "/kb/rules", timeout=20)
    rules = (r.data or {}).get("rules", []) if r.ok and isinstance(r.data, dict) else []
    if not r.ok:
        warn_api(r, t("kb.tab_rules"))
    cps = sorted({x.get("checkpoint") for x in rules if x.get("checkpoint")})
    cp = st.selectbox(t("kb.checkpoint"), [""] + cps, format_func=lambda x: x or t("search.any"))
    shown = [x for x in rules if not cp or x.get("checkpoint") == cp]
    st.markdown(f'**{len(shown)}** {esc(t("kb.rules_count"))}')
    for rule in shown:
        title = f'{rule["id"]} · {rule.get("action")} · {rule.get("checkpoint")}'
        with st.expander(title):
            flags = badge(str(rule.get("action")), ACTION_COLOR.get(rule.get("action"), GREY))
            if rule.get("proposal"):
                flags += badge(t("kb.proposal"), AMBER)
            if rule.get("kind"):
                flags += chip(str(rule["kind"]))
            if rule.get("gap_id"):
                flags += chip(f'gap {rule["gap_id"]}')
            if rule.get("pipeline_node"):
                flags += chip(f'Y.3172 {rule["pipeline_node"]}')
            if rule.get("readiness_dims"):
                flags += chip("AI Readiness " + ", ".join(str(d) for d in rule["readiness_dims"]))
            st.markdown(flags, unsafe_allow_html=True)
            st.markdown(f'**{esc(pick(rule.get("message_fr"), rule.get("message_en")))}**', unsafe_allow_html=True)
            st.code(str(rule.get("when")), language="python")
            if rule.get("note"):
                st.caption(rule["note"])
            ids = rule.get("justified_chunks") or [j.get("chunk_id") for j in rule.get("justification", [])]
            if ids:
                st.caption(t("rules.justified_by"))
            for cid in [i for i in ids if i]:
                with st.container(border=True):
                    render_chunk(cid)

# ------------------------------------------------------------------ sources
with tab_sources:
    r = api("GET", "/kb/sources", timeout=20)
    srcs = (r.data or {}).get("sources", []) if r.ok and isinstance(r.data, dict) else []
    if not r.ok:
        warn_api(r, t("kb.tab_sources"))
    st.markdown(f'**{len(srcs)}** {esc(t("kb.sources_count"))}')
    rows = ""
    for s in srcs:
        link = f'<a href="{esc(s.get("url"))}" target="_blank">{esc(t("kb.source_link"))}</a>' if s.get("url") else ""
        status = s.get("status") or s.get("build_status") or ""
        col = GREEN if str(status) in ("ok", "downloaded", "built") else AMBER
        rows += (f'<tr><td><b>{esc(s.get("id"))}</b></td><td>{esc(s.get("title"))}<br><span style="color:#777">'
                 f'{esc(s.get("issuer"))} · {esc(s.get("date"))}</span></td>'
                 f'<td>{badge(t("kb.binding") if s.get("binding") else t("kb.nonbinding"), GREEN if s.get("binding") else GREY)}'
                 f'{chip(str(s.get("kind") or ""))}</td><td>{badge(str(status), col)}</td>'
                 f'<td>{esc(s.get("chunks") if s.get("chunks") is not None else "")}</td><td>{link}</td></tr>')
    head = "".join(f"<th>{esc(t(k))}</th>" for k in ("kb.col_id", "kb.col_title", "kb.col_status", "kb.col_build",
                                                      "kb.col_chunks", "kb.col_url"))
    st.markdown(f'<table class="vd-table"><tr>{head}</tr>{rows}</table>', unsafe_allow_html=True)
