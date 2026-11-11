"""Page 1 - Verify a diploma: form + consent -> live agent timeline -> verdict, checks, evidence, rules, gaps."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from common import (STRETCH, api, api_error_text, case_result, esc, short_id, warn_api)  # noqa: E402
from common import setup_page  # noqa: E402
from i18n import t  # noqa: E402
from render import render_result, timeline_html  # noqa: E402
from samples import SAMPLES, load_samples  # noqa: E402

setup_page("page.verify.title", "page.verify.sub")

PURPOSES = ["recruitment", "admission", "equivalence", "marketing"]
REQ_TYPES = ["employer", "university", "embassy", "ministry", "hr_firm"]
COUNTRIES = ["CM", "FR", "AE", "GB", "US", "NG", "CA", "DE"]
FIELD_KEYS = ["holder_name", "birth_date", "birth_place", "institution_id", "programme_id", "session_year", "average",
              "mention", "serial", "signatory_name", "issue_date"]
TERMINAL_BAD = ("error",)


def apply_sample() -> None:
    """Callback: fill the form from the picked sample."""
    sid = st.session_state.get("v_sample", "")
    s = next((x for x in st.session_state.get("v_samples", SAMPLES) if x["diploma_case_id"] == sid), None)
    for k in FIELD_KEYS:
        v = (s or {}).get("fields", {}).get(k)
        st.session_state[f"v_{k}"] = "" if v is None else str(v)


if "v_samples" not in st.session_state:
    st.session_state["v_samples"] = load_samples()
samples = st.session_state["v_samples"]

# ---------------------------------------------------------------- form
left, right = st.columns([3, 2], gap="large")
with left:
    st.subheader(t("verify.input"))
    st.selectbox(t("verify.sample"), [""] + [s["diploma_case_id"] for s in samples], key="v_sample", on_change=apply_sample,
                 format_func=lambda c: t("verify.sample_none") if not c else
                 next((f'{s["diploma_case_id"]} · {s.get("label", "")}' for s in samples if s["diploma_case_id"] == c), c))
    st.caption(t("verify.sample_help"))
    upload = st.file_uploader(t("verify.upload"), type=["png", "jpg", "jpeg", "pdf"], key="v_upload")
    c1, c2, c3 = st.columns(3)
    for i, k in enumerate(FIELD_KEYS):
        [c1, c2, c3][i % 3].text_input(t(f"field.{k}"), key=f"v_{k}")

with right:
    st.subheader(t("verify.request"))
    consent_on = st.checkbox(t("verify.consent_present"), value=True, key="v_consent_on")
    st.text_input(t("verify.consent_token"), value="CONSENT-DEMO-0A1B2C3D4E5F", help=t("verify.consent_help"), key="v_consent", disabled=not consent_on)
    st.text_input(t("verify.legal_basis"), key="v_legal_basis", help=t("verify.legal_basis_help"))
    st.selectbox(t("verify.purpose"), PURPOSES, key="v_purpose", format_func=lambda x: t(f"purpose.{x}"))
    st.selectbox(t("verify.requester_type"), REQ_TYPES, key="v_rtype", format_func=lambda x: t(f"rtype.{x}"))
    st.selectbox(t("verify.country"), COUNTRIES, key="v_country")
    st.radio(t("verify.mode"), ["on", "off"], key="v_mode", horizontal=True,
             format_func=lambda m: t("mode.on") if m == "on" else t("mode.off"))
    run = st.button(t("verify.run"), type="primary", **STRETCH)


def build_request() -> tuple[dict, dict | None]:
    """Return (form-data dict, files dict or None) for POST /cases."""
    ss = st.session_state
    fields = {}
    for k in FIELD_KEYS:
        v = str(ss.get(f"v_{k}", "")).strip()
        if v:
            fields[k] = float(v.replace(",", ".")) if k == "average" else int(v) if k == "session_year" else v
    body: dict = {"purpose": ss["v_purpose"], "requester_type": ss["v_rtype"], "requester_country": ss["v_country"],
                  "lang": st.session_state.get("_lang", "fr")}
    if ss.get("v_consent_on") and str(ss.get("v_consent", "")).strip():
        body["consent_token"] = ss["v_consent"].strip()
    if str(ss.get("v_legal_basis", "")).strip():
        body["legal_basis"] = ss["v_legal_basis"].strip()
    if ss.get("v_sample"):
        body["diploma_case_id"] = ss["v_sample"]
    if fields:
        body["fields"] = fields
    return body, None


def run_verification() -> None:
    ss = st.session_state
    try:
        body, _ = build_request()
    except ValueError:
        st.warning(t("verify.bad_number"))
        return
    up = ss.get("v_upload")
    if not (body.get("fields") or body.get("diploma_case_id") or up):
        st.warning(t("verify.need_input"))
        return
    # sample picked and form untouched -> let the API load the fixture; otherwise typed fields drive the case
    if body.get("diploma_case_id") and not up:
        sample = next((s for s in samples if s["diploma_case_id"] == body["diploma_case_id"]), None)
        if sample and body.get("fields") == {k: v for k, v in sample.get("fields", {}).items() if k in FIELD_KEYS and v not in (None, "")}:
            body.pop("fields")
    if up is not None:
        import json as _json
        form = {k: str(v) for k, v in body.items() if k != "fields"}
        if body.get("fields"):
            form["fields"] = _json.dumps(body["fields"])
        r = api("POST", "/cases", data=form, files={"diploma": (up.name, up.getvalue(), up.type or "application/octet-stream")},
                timeout=60)
    else:
        r = api("POST", "/cases", json=body, timeout=30)
    if not r.ok or not isinstance(r.data, dict) or "case_id" not in r.data:
        warn_api(r, t("verify.creating"))
        if r.status == 422:
            st.caption(api_error_text(r))
        return
    cid = r.data["case_id"]
    ss["verify_case_id"] = cid
    ss.pop("verify_result", None)
    r = api("POST", f"/cases/{cid}/run", params={"policy_mode": ss["v_mode"], "wait": "false"}, timeout=30)
    if not r.ok:
        warn_api(r, t("verify.starting"))
        if r.status == 422:
            st.caption(api_error_text(r))
        return
    res = {}
    with st.status(t("timeline.title"), expanded=True) as box:
        ph = st.empty()
        t0 = time.time()
        while time.time() - t0 < 240:
            g = api("GET", f"/cases/{cid}", timeout=20)
            if not g.ok:
                warn_api(g, t("verify.polling"))
                box.update(state="error")
                return
            res = case_result(g.data)
            running = res.get("status") in ("created", "running", None)
            ph.markdown(timeline_html(res.get("steps") or [], running=running), unsafe_allow_html=True)
            if not running:
                break
            time.sleep(0.5)
        box.update(label=t("timeline.done"), state="error" if res.get("status") in TERMINAL_BAD else "complete",
                   expanded=False)
    ss["verify_result"] = res


if run:
    run_verification()

res = st.session_state.get("verify_result")
if res:
    st.divider()
    st.subheader(f'{t("verify.result")} · {short_id(res.get("case_id"))}')
    render_result(res, key="verify")
else:
    st.info(t("verify.empty"))
