"""Page 3 - Registrar console: queue of cases awaiting review, approve/reject with a mandatory justification."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from common import (AMBER, RED, STRETCH, api, badge, case_result, chip, esc, pick, setup_page, short_id,  # noqa: E402
                    verdict_badge, warn_api)
from i18n import t  # noqa: E402
from render import render_result  # noqa: E402

setup_page("page.registrar.title", "page.registrar.sub")
MIN_CHARS = 20  # server-side minimum (PR-APPROVAL-AUDIT-001); the UI refuses an empty justification before sending

top = st.columns([4, 1])
if top[1].button(t("registrar.refresh"), **STRETCH):
    st.rerun()

if st.session_state.get("reg_flash"):
    st.success(st.session_state.pop("reg_flash"))

tab_pending, tab_done = st.tabs([t("registrar.tab_pending"), t("registrar.tab_done")])


def list_cases(review_state: str) -> list:
    r = api("GET", "/cases", params={"review_state": review_state, "limit": 100}, timeout=15)
    if r.status in (404, 501):
        return []  # endpoint not ready: behave as an empty queue
    if not r.ok:
        warn_api(r, t("registrar.queue"))
        return []
    return r.data if isinstance(r.data, list) else (r.data or {}).get("cases", [])


def queue_label(c: dict) -> str:
    return (f'{short_id(c.get("case_id"))} · {c.get("holder") or "?"} · {c.get("institution_id") or "?"} · '
            f'{c.get("verdict") or "?"}' + (f' · {c.get("scenario_id")}' if c.get("scenario_id") else ""))


with tab_pending:
    pending = list_cases("pending")
    if not pending:
        st.info(t("registrar.empty"))
    else:
        st.markdown(f'**{len(pending)}** {esc(t("registrar.waiting"))}')
        ids = [c["case_id"] for c in pending]
        sel = st.selectbox(t("registrar.pick"), ids, format_func=lambda cid: queue_label(next(c for c in pending if c["case_id"] == cid)),
                           key="reg_sel")
        g = api("GET", f"/cases/{sel}", timeout=20)
        if not g.ok:
            warn_api(g, t("registrar.case"))
        else:
            res = case_result(g.data)
            diploma = g.data.get("diploma_fields") or {}
            left, right = st.columns([3, 2], gap="large")
            with left:
                render_result(res, key=f"reg-{sel}")
            with right:
                st.markdown(f"### {t('registrar.diploma')}")
                if diploma:
                    rows = "".join(f"<tr><td><b>{esc(t('field.' + k))}</b></td><td>{esc(v)}</td></tr>" for k, v in diploma.items()
                                   if k in ("holder_name", "institution_id", "programme_id", "session_year", "average", "mention",
                                            "serial", "signatory_name") and v is not None)
                    st.markdown(f'<table class="vd-table">{rows}</table>', unsafe_allow_html=True)
                req = g.data.get("request") or {}
                if req:
                    st.caption(f'{t("verify.purpose")}: {req.get("purpose")} · {t("verify.requester_type")}: '
                               f'{req.get("requester_type")} · {req.get("requester_country")}')
                st.markdown(f"### {t('registrar.decision')}")
                reviewer = st.text_input(t("registrar.reviewer"), value="registrar", key=f"rv-{sel}")
                decision = st.radio(t("registrar.decision_label"), ["approve", "reject"], horizontal=True, key=f"dec-{sel}",
                                    format_func=lambda d: t(f"registrar.{d}"))
                just = st.text_area(t("registrar.justification"), key=f"just-{sel}", height=140,
                                    placeholder=t("registrar.just_placeholder"))
                n = len(just.strip())
                st.caption(t("registrar.just_counter", n=n, min=MIN_CHARS))
                ok_to_send = n > 0
                if st.button(t("registrar.submit"), type="primary", disabled=not ok_to_send, key=f"sub-{sel}", **STRETCH):
                    rr = api("POST", f"/cases/{sel}/review", json={"decision": decision, "justification": just.strip(),
                                                                    "reviewer": reviewer or "registrar"}, timeout=30)
                    body = rr.data if isinstance(rr.data, dict) else {}
                    if rr.ok and body.get("accepted", True):
                        st.session_state["reg_flash"] = t("registrar.accepted", decision=t(f"registrar.{decision}"))
                        st.rerun()
                    elif rr.status == 422 or body.get("accepted") is False:
                        st.error(pick(body.get("message_fr"), body.get("message_en")) or t("registrar.rejected_generic"))
                        if (body.get("review") or {}).get("supervisor_alert"):
                            st.warning(t("registrar.supervisor_alert"))
                    else:
                        warn_api(rr, t("registrar.submit"))
                if not ok_to_send:
                    st.caption(t("registrar.required"))
                with st.expander(t("registrar.test_expander")):
                    st.caption(t("registrar.test_help"))
                    if st.button(t("registrar.test_button"), key=f"test-{sel}"):
                        rr = api("POST", f"/cases/{sel}/review", json={"decision": "approve", "justification": "",
                                                                        "reviewer": reviewer or "registrar"}, timeout=30)
                        body = rr.data if isinstance(rr.data, dict) else {}
                        if rr.status == 0:
                            warn_api(rr, t("registrar.submit"))
                        elif body.get("accepted") is False or rr.status == 422:
                            st.error(pick(body.get("message_fr"), body.get("message_en")) or t("registrar.rejected_generic"))
                            if (body.get("review") or {}).get("supervisor_alert"):
                                st.warning(t("registrar.supervisor_alert"))
                        else:
                            st.warning(t("registrar.test_accepted"))

with tab_done:
    rows = []
    for state in ("approved", "rejected"):
        rows += [(state, c) for c in list_cases(state)]
    if not rows:
        st.info(t("registrar.none_done"))
    for state, c in rows:
        with st.expander(f'{queue_label(c)} · {t("registrar." + ("approve" if state == "approved" else "reject"))}'):
            g = api("GET", f"/cases/{c['case_id']}", timeout=20)
            rev = (g.data or {}).get("review") if g.ok and isinstance(g.data, dict) else None
            if rev:
                st.markdown(badge(state, AMBER if state == "rejected" else "#1b8a4b") + chip(str(rev.get("reviewer"))),
                            unsafe_allow_html=True)
                st.write(rev.get("justification") or "")
                st.caption(str(rev.get("ts") or ""))
            else:
                st.caption(t("registrar.no_review_detail"))
