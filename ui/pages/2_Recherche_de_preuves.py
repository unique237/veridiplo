"""Page 2 - Evidence search engine over the archives (hybrid FTS + vector + trigram) with page crops."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from common import STRETCH, api, chip, esc, setup_page, warn_api  # noqa: E402
from i18n import t  # noqa: E402
from render import evidence_card  # noqa: E402

setup_page("page.search.title", "page.search.sub")

fr = api("GET", "/search/filters", timeout=10)
filters = fr.data if fr.ok and isinstance(fr.data, dict) else {}
if not fr.ok:
    warn_api(fr, t("search.filters"))
ALL = ""
inst_opts = [ALL] + list(filters.get("institutions") or [])
year_opts = [ALL] + [str(y) for y in (filters.get("years") or [])]
by_inst = filters.get("programmes_by_institution") or {}

with st.form("search_form"):
    q = st.text_input(t("search.query"), value=st.session_state.get("s_q", ""), placeholder=t("search.placeholder"))
    c1, c2, c3, c4 = st.columns(4)
    inst = c1.selectbox(t("search.institution"), inst_opts, format_func=lambda x: x or t("search.any"))
    year = c2.selectbox(t("search.year"), year_opts, format_func=lambda x: x or t("search.any"))
    progs = [ALL] + (by_inst.get(inst) if inst and by_inst.get(inst) else list(filters.get("programmes") or []))
    prog = c3.selectbox(t("search.programme"), progs, format_func=lambda x: x or t("search.any"))
    limit = c4.slider(t("search.limit"), 3, 30, 10)
    go = st.form_submit_button(t("search.go"), type="primary")

if go:
    st.session_state["s_q"] = q
    if not q.strip():
        st.warning(t("search.need_query"))
    else:
        params = {"q": q.strip(), "limit": limit}
        if inst:
            params["institution"] = inst
        if year:
            params["year"] = year
        if prog:
            params["programme"] = prog
        r = api("GET", "/search", params=params, timeout=30)
        if not r.ok or not isinstance(r.data, dict):
            warn_api(r, t("search.title_endpoint"))
        else:
            st.session_state["s_res"] = r.data

data = st.session_state.get("s_res")
if data:
    results = data.get("results") or []
    st.markdown(f'**{len(results)}** {esc(t("search.results"))} · {data.get("took_ms", "?")} ms · «{esc(data.get("query", ""))}»',
                unsafe_allow_html=True)
    if not results:
        st.info(t("search.no_results"))
    for i, r in enumerate(results):
        with st.container(border=True):
            left, right = st.columns([3, 2], gap="large")
            with left:
                head = (chip(r.get("institution_id") or "?") + chip(str(r.get("session_year") or "?")) +
                        chip(r.get("programme_id") or "?") + chip(r.get("kind") or ""))
                st.markdown(f'**#{i + 1}** &nbsp; {head}', unsafe_allow_html=True)
                loc = f'{r.get("source_file", "")} · p.{r.get("page")}' + (f' · {t("evidence.row")} {r.get("row_idx")}'
                                                                           if r.get("row_idx") is not None else "")
                st.caption(loc)
                st.markdown(f'<div class="vd-text" style="font-size:.95rem">{esc(r.get("snippet"))}</div>', unsafe_allow_html=True)
                sb = r.get("score_breakdown") or {}
                if sb:
                    st.caption(f'RRF {sb.get("rrf")} · fts {sb.get("fts")} · vector {sb.get("vector")} · trgm {sb.get("trgm")}'
                               f' · {t("search.score")} {r.get("score")}')
                if r.get("fields"):
                    with st.expander(t("evidence.fields")):
                        st.json({k: v for k, v in r["fields"].items() if not str(k).startswith("_")}, expanded=False)
            with right:
                if r.get("crop_url"):
                    from common import fetch_bytes
                    img = fetch_bytes(r["crop_url"])
                    if img:
                        st.image(img, caption=t("evidence.crop_caption"), **STRETCH)
                    else:
                        st.caption(t("evidence.crop_missing"))
else:
    st.info(t("search.empty"))
