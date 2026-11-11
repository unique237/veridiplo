"""Reusable renderers for a CaseResult (timeline, verdict, checks, evidence, rules, gaps, shared output)."""
from __future__ import annotations

import json
from typing import Any, Optional

import streamlit as st

from common import (STRETCH, AMBER, GREEN, GREY, PII_FIELDS, RED, api, badge, chip, esc, fetch_bytes, fetch_chunk, mark_accusatory,
                    node_chip, pick, short_id, verdict_box, warn_api)
from i18n import get_lang, t

ACTION_COLOR = {"allow": GREEN, "redact": AMBER, "require_consent": AMBER, "block": RED, "escalate": "#7a3db8"}


# ---------------------------------------------------------------------------------------------------- timeline
def timeline_html(steps: list[dict], running: bool = False) -> str:
    rows = []
    for s in steps:
        ms = f'{s.get("ms")} ms' if s.get("ms") is not None else ""
        rows.append(f'<div class="vd-step">{node_chip(s.get("node", ""))}<div class="txt"><b>{esc(s.get("agent"))}</b> '
                    f'· {esc(s.get("action"))}<br><span style="color:#555">{esc(s.get("summary"))}</span></div>'
                    f'<div class="ms">{esc(ms)}</div></div>')
    if running:
        rows.append(f'<div class="vd-step"><div class="node" style="background:#bbb">…</div>'
                    f'<div class="txt"><i>{esc(t("timeline.running"))}</i></div></div>')
    return "".join(rows) or f'<i>{esc(t("timeline.empty"))}</i>'


# ---------------------------------------------------------------------------------------------------- verdict
def render_verdict(res: dict, big: bool = False) -> None:
    v = res.get("verdict") or {}
    label = "BLOCKED" if (res.get("blocked") and not v) else v.get("label")
    if res.get("shared_output", {}).get("scope") == "attestation" and not v:
        label = "ATTESTATION"
    if not v and not label and res.get("shared_output", {}).get("scope") == "bulk_records":
        label = "RECORDS_RETURNED"
    reasons = pick(v.get("reasons_fr"), v.get("reasons_en")) or []
    sub = esc(" ".join(reasons)) if reasons else ""
    if v.get("confidence") is not None and v and label != "BLOCKED":
        sub = f'{esc(t("verdict.confidence"))}: <b>{float(v.get("confidence", 0)):.0%}</b>' + (f' · {sub}' if sub else "")
    st.markdown(verdict_box(label, sub, big), unsafe_allow_html=True)
    chips = [chip(f'{t("policy_mode")}: {"B (on)" if res.get("policy_mode") == "on" else "A (off)"}')]
    if v.get("needs_human"):
        chips.append(badge(t("flag.needs_human"), AMBER))
    if res.get("needs_registrar"):
        chips.append(badge(t("flag.registrar"), "#7a3db8"))
    if res.get("holder_notified"):
        chips.append(badge(t("flag.holder_notified"), GREEN))
    if res.get("transfer_flag"):
        chips.append(badge(t("flag.transfer"), RED))
    if res.get("blocked"):
        chips.append(badge(t("flag.blocked"), GREY))
    if res.get("report_sha256"):
        chips.append(chip("sha256 " + str(res["report_sha256"])[:12], str(res["report_sha256"])))
    st.markdown(" ".join(chips), unsafe_allow_html=True)


def render_explanation(res: dict) -> None:
    txt = pick(res.get("explanation_fr"), res.get("explanation_en"))
    other = (res.get("explanation_en") if get_lang() == "fr" else res.get("explanation_fr")) or ""
    if txt:
        st.markdown(f'<div class="vd-text">{esc(txt)}</div>', unsafe_allow_html=True)
    if other and other != txt:
        with st.expander(t("explanation.other_lang")):
            st.write(other)
    if res.get("archive_request_draft"):
        with st.expander(t("archive_draft"), expanded=False):
            st.code(res["archive_request_draft"], language=None)


# ---------------------------------------------------------------------------------------------------- checks
def render_checks(res: dict) -> None:
    checks = res.get("checks") or []
    if not checks:
        st.caption(t("checks.none"))
        return
    sym = {"pass": ('<span class="vd-pass">✔ ' + esc(t("status.pass")) + "</span>"),
           "fail": ('<span class="vd-fail">✘ ' + esc(t("status.fail")) + "</span>"),
           "unknown": ('<span class="vd-unk">? ' + esc(t("status.unknown")) + "</span>")}
    rows = ""
    for c in checks:
        det = pick(c.get("detail"), c.get("detail_en"))
        sc = "" if c.get("score") is None else f'{float(c["score"]):.2f}'
        rows += (f'<tr><td><b>{esc(c.get("check_id"))}</b></td><td>{esc(c.get("field"))}</td>'
                 f'<td>{sym.get(c.get("status"), esc(c.get("status")))}</td>'
                 f'<td>{"●" if c.get("critical", True) else "○"}</td><td>{esc(sc)}</td>'
                 f'<td>{esc(", ".join(c.get("evidence_ids") or []))}</td><td>{esc(det)}</td></tr>')
    head = "".join(f"<th>{esc(t(k))}</th>" for k in ("checks.id", "checks.field", "checks.status", "checks.critical",
                                                      "checks.score", "checks.evidence", "checks.detail"))
    st.markdown(f'<table class="vd-table"><tr>{head}</tr>{rows}</table>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------------------------------- evidence
def render_evidence(res: dict, key: str = "ev") -> None:
    ev = res.get("evidence") or []
    if not ev:
        st.caption(t("evidence.none"))
        return
    cols = st.columns(2)
    for i, e in enumerate(ev):
        with cols[i % 2]:
            evidence_card(e, f"{key}-{i}")


def evidence_card(e: dict, key: str = "ev", show_fields: bool = True) -> None:
    with st.container(border=True):
        loc = []
        if e.get("source_file"):
            loc.append(str(e["source_file"]))
        if e.get("page") is not None:
            loc.append(f'p.{e["page"]}')
        if e.get("row_idx") is not None:
            loc.append(f'{t("evidence.row")} {e["row_idx"]}')
        head = f'<b>{esc(e.get("evidence_id", ""))}</b> · {chip(e.get("kind", ""))}'
        for f in e.get("supports") or []:
            head += badge(f"+ {f}", GREEN)
        for f in e.get("contradicts") or []:
            head += badge(f"− {f}", RED)
        st.markdown(head, unsafe_allow_html=True)
        st.caption(" · ".join(loc) or t("evidence.no_loc"))
        if e.get("snippet"):
            st.markdown(f'<div class="vd-text" style="font-size:.9rem">{esc(e["snippet"])}</div>', unsafe_allow_html=True)
        if e.get("crop_url"):
            img = fetch_bytes(e["crop_url"])
            if img:
                st.image(img, caption=t("evidence.crop_caption"), **STRETCH)
            else:
                st.caption(t("evidence.crop_missing"))
        meta = []
        if e.get("bbox"):
            meta.append("bbox=" + ", ".join(f"{x:.0f}" for x in e["bbox"]))
        if e.get("sha256"):
            meta.append("sha256=" + str(e["sha256"])[:16] + "…")
        if e.get("ocr_conf") is not None:
            meta.append(f'OCR {float(e["ocr_conf"]):.0f}')
        if e.get("score") is not None:
            meta.append(f'score {float(e["score"]):.3f}')
        if meta:
            st.caption(" · ".join(meta))
        if show_fields and e.get("fields"):
            with st.expander(t("evidence.fields")):
                st.json({k: v for k, v in e["fields"].items() if not str(k).startswith("_")}, expanded=False)


# ---------------------------------------------------------------------------------------------------- rules
def chunk_badges(c: dict) -> str:
    out = badge(t("kb.binding") if c.get("binding") else t("kb.nonbinding"), GREEN if c.get("binding") else GREY)
    out += chip(t("kb.kind_summary") if c.get("kind") == "summary" else t("kb.kind_primary"))
    if c.get("as_reported"):
        out += badge(t("kb.as_reported"), AMBER)
    if c.get("article"):
        out += chip(str(c["article"]))
    return out


def render_chunk(chunk_id: str) -> None:
    c = fetch_chunk(chunk_id)
    if not c:
        st.caption(f"{chunk_id} — {t('kb.chunk_unavailable')}")
        return
    st.markdown(f'**{esc(chunk_id)}** — {esc(c.get("title"))}<br>{chunk_badges(c)}', unsafe_allow_html=True)
    if c.get("issuer") or c.get("date"):
        st.caption(f'{c.get("issuer") or ""} · {c.get("date") or ""} · {c.get("jurisdiction") or ""}')
    st.markdown(f'<div class="vd-text" style="font-size:.9rem;max-height:14rem;overflow:auto">{esc(c.get("text"))}</div>',
                unsafe_allow_html=True)
    if c.get("url"):
        st.markdown(f'[{t("kb.source_link")}]({c["url"]})')


def render_rules(res: dict) -> None:
    fired: dict[str, dict] = {}
    for f in res.get("fired_rules") or []:
        fired.setdefault(f["rule_id"], f)
    for d in res.get("decisions") or []:
        for f in d.get("fired") or []:
            fired.setdefault(f["rule_id"], f)
    decisions = res.get("decisions") or []
    if decisions:
        chips = " ".join(badge(f'{d.get("checkpoint")}: {d.get("action")}', ACTION_COLOR.get(d.get("action"), GREY))
                         for d in decisions)
        st.markdown(chips, unsafe_allow_html=True)
    if not fired:
        st.caption(t("rules.none") if res.get("policy_mode") == "on" else t("rules.off"))
        return
    for rid, f in fired.items():
        title = f'{rid} · {f.get("action")} · {f.get("checkpoint")}'
        with st.expander(title):
            st.markdown(badge(str(f.get("action")), ACTION_COLOR.get(f.get("action"), GREY)) +
                        (badge(t("kb.proposal"), AMBER) if f.get("proposal") else ""), unsafe_allow_html=True)
            st.markdown(f'**{esc(pick(f.get("message_fr"), f.get("message_en")))}**', unsafe_allow_html=True)
            ids = f.get("kb_chunk_ids") or []
            if ids:
                st.caption(t("rules.justified_by"))
                for cid in ids:
                    with st.container(border=True):
                        render_chunk(cid)


# ---------------------------------------------------------------------------------------------------- gaps
def render_gaps(res: dict) -> None:
    gaps = res.get("gaps") or []
    if not gaps:
        st.caption(t("gaps.none"))
        return
    for g in gaps:
        d = g.get("detail") or {}
        with st.container(border=True):
            st.markdown(badge(str(g.get("gap_id") or "?"), "#7a3db8") + chip(g.get("type", "")) +
                        chip(g.get("checkpoint") or "") + f' **{esc(g.get("title"))}**', unsafe_allow_html=True)
            if d.get("reason"):
                st.caption(str(d["reason"]))
            if d.get("proposal"):
                st.markdown(f'*{esc(t("gaps.proposal"))}*: {esc(d["proposal"])}', unsafe_allow_html=True)


# ---------------------------------------------------------------------------------------------------- shared output
SCOPE_COLOR = {"full_record": RED, "bulk_records": RED, "evidence_pack": GREEN, "attestation": "#1f5fa8", "none": GREY}


def shared_summary(res: dict) -> dict[str, Any]:
    so = res.get("shared_output") or {}
    keys = [k for k in so if k not in ("explanation_fr", "explanation_en", "rule_ids", "status", "reason_fr", "reason_en")]
    pii = [k for k in keys if k in PII_FIELDS]
    return {"scope": so.get("scope"), "fields": keys, "pii": pii, "records": so.get("records_returned"),
            "status": so.get("status")}


def render_shared(res: dict, highlight_pii: bool = True) -> None:
    s = shared_summary(res)
    if not res.get("shared_output"):
        st.caption(t("shared.none"))
        return
    scope = s["scope"] or "none"
    html_ = badge(t(f"scope.{scope}"), SCOPE_COLOR.get(scope, GREY))
    if s["status"]:
        html_ += chip(str(s["status"]))
    if s["records"] is not None:
        html_ += chip(f'{s["records"]} {t("shared.records")}')
    st.markdown(html_, unsafe_allow_html=True)
    if s["fields"]:
        parts = []
        for k in s["fields"]:
            if highlight_pii and k in PII_FIELDS:
                parts.append(f'<span class="vd-chip" style="background:#fdeaea;border-color:#e8a0a0;color:#8a0000">{esc(k)}</span>')
            else:
                parts.append(chip(k))
        st.markdown(" ".join(parts), unsafe_allow_html=True)
    if highlight_pii and s["pii"]:
        st.caption(t("shared.pii_warning", n=len(s["pii"])))
    with st.expander(t("shared.raw")):
        st.json(res.get("shared_output"), expanded=False)


# ---------------------------------------------------------------------------------------------------- downloads
@st.cache_data(ttl=60, show_spinner=False)
def fetch_pack(case_id: str, status: Optional[str] = None) -> Optional[dict]:
    """Server-built evidence pack (with integrity hash); None if unavailable (the local result is used instead)."""
    r = api("GET", f"/cases/{case_id}/report.json", params={"audience": "registrar"}, timeout=30)
    return r.data if r.ok and isinstance(r.data, dict) else None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_pdf(case_id: str, lang: str, status: Optional[str] = None) -> Optional[bytes]:
    """PDF report from the API (verifier audience first, registrar audience if the result is held). Cached briefly."""
    for aud in ("verifier", "registrar"):
        r = api("GET", f"/cases/{case_id}/report.pdf", params={"audience": aud, "lang": lang}, timeout=40)
        if r.ok and r.content:
            return r.content
    return None


def render_downloads(res: dict, key: str = "dl") -> None:
    cid = res.get("case_id")
    c1, c2 = st.columns(2)
    pack = fetch_pack(cid, res.get("status")) if cid else None
    c1.download_button(t("download.json"), json.dumps(pack or res, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                       file_name=f"veridiplo-{short_id(cid)}.json", mime="application/json", key=f"{key}-json",
                       **STRETCH)
    pdf = fetch_pdf(cid, get_lang(), res.get("status")) if cid else None
    if pdf:
        c2.download_button(t("download.pdf"), pdf, file_name=f"veridiplo-{short_id(cid)}.pdf", mime="application/pdf",
                           key=f"{key}-pdf", **STRETCH)
    else:
        c2.button(t("download.pdf_unavailable"), disabled=True, key=f"{key}-pdf-na", **STRETCH)


# ---------------------------------------------------------------------------------------------------- full result
def render_result(res: dict, key: str = "r", show_timeline: bool = True) -> None:
    """Complete case result: verdict, explanation, held notice, checks, evidence, rules, gaps, communicated output."""
    render_verdict(res, big=True)
    if res.get("status") == "pending_review" or (res.get("needs_registrar") and res.get("policy_mode") == "on"):
        st.info(t("held.notice"))
    if res.get("status") == "error":
        st.error(t("err.case_error"))
    render_explanation(res)
    tabs = st.tabs([t("tab.checks"), t("tab.evidence"), t("tab.rules"), t("tab.gaps"), t("tab.shared"), t("tab.timeline")])
    with tabs[0]:
        render_checks(res)
    with tabs[1]:
        render_evidence(res, key)
    with tabs[2]:
        render_rules(res)
    with tabs[3]:
        render_gaps(res)
    with tabs[4]:
        st.caption(t("shared.caption"))
        render_shared(res)
    with tabs[5]:
        st.markdown(timeline_html(res.get("steps") or []), unsafe_allow_html=True)
    render_downloads(res, key)
