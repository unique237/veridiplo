"""Page 5 - Scenarios A/B (demo centrepiece): same input, Version A (ungoverned) vs Version B (policy-governed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from common import (AMBER, GREEN, GREY, PII_FIELDS, RED, STRETCH, api, api_error_text, badge, case_result, chip, esc,  # noqa: E402
                    mark_accusatory, pick, setup_page, verdict_box, verdict_name, warn_api)
from i18n import get_lang, t  # noqa: E402
from render import ACTION_COLOR, SCOPE_COLOR  # noqa: E402

setup_page("page.ab.title", "page.ab.sub")

DEFAULT_SCENARIOS = [{"id": f"S{i}"} for i in range(1, 8)]
COL_A, COL_B = "#a8321f", "#1b6f8a"


# ---------------------------------------------------------------------------------------------------- data helpers
def result_of(run: dict) -> dict:
    """A scenario run record holds the CaseResult under 'result'; accept a bare CaseResult too."""
    return case_result(run.get("result") if isinstance(run.get("result"), dict) else run)


def summary(run: dict) -> dict:
    r = result_of(run)
    so = r.get("shared_output") or {}
    v = r.get("verdict") or {}
    label = v.get("label") or ("BLOCKED" if r.get("blocked") else ("ATTESTATION" if so.get("scope") == "attestation" else
                                                                  ("RECORDS_RETURNED" if so.get("scope") == "bulk_records" else None)))
    text = pick(so.get("explanation_fr") or r.get("explanation_fr"), so.get("explanation_en") or r.get("explanation_en")) or ""
    if get_lang() == "en":
        text = so.get("explanation_en") or r.get("explanation_en") or text
    else:
        text = so.get("explanation_fr") or r.get("explanation_fr") or text
    rules = sorted({f["rule_id"]: f for f in (r.get("fired_rules") or [])}.items())
    gaps = {g.get("gap_id"): g for g in (r.get("gaps") or []) if g.get("gap_id")}
    keys = [k for k in so if k not in ("explanation_fr", "explanation_en", "rule_ids", "reason_fr", "reason_en", "status")]
    review = run.get("review") or {}
    return {"r": r, "label": label, "conf": v.get("confidence") if label not in ("BLOCKED", "RECORDS_RETURNED") else None, "text": text, "n_bad": mark_accusatory(text)[1],
            "scope": so.get("scope") or "none", "fields": keys, "records": so.get("records_returned"),
            "rules": dict(rules), "gaps": gaps, "registrar": bool(r.get("needs_registrar")),
            "holder": bool(r.get("holder_notified")), "transfer": bool(r.get("transfer_flag")),
            "blocked": bool(r.get("blocked")), "review": review, "chain": run.get("audit_chain_valid"),
            "audit_n": run.get("audit_n"), "status": run.get("status") or r.get("status")}


def hl(html: str, differs: bool) -> str:
    return f'<span class="vd-diff">{html}</span>' if differs else html


def yes_no(v: bool) -> str:
    return f'<span class="{"vd-ok" if v else "vd-ko"}">{"✔" if v else "✘"}</span>'


# ---------------------------------------------------------------------------------------------------- rendering
def headline(a: dict, b: dict) -> None:
    """Row of 'what changed' tiles."""
    def tile(label: str, va: str, vb: str, differs: bool) -> str:
        arrow = f'<span style="color:{GREEN if differs else GREY};font-size:1.6rem"> → </span>'
        bg = "#fff8d6" if differs else "#f4f6f9"
        return (f'<div style="flex:1;min-width:12rem;background:{bg};border-radius:12px;padding:.6rem .9rem;'
                f'border:1px solid #e0d9a8"><div style="font-size:.78rem;color:#5f6b7a;text-transform:uppercase;'
                f'letter-spacing:.05em">{esc(label)}</div><div style="font-size:1.15rem;font-weight:700;margin-top:.2rem">'
                f'{va}{arrow}{vb}</div></div>')
    tiles = [
        tile(t("ab.verdict"), esc(verdict_name(a["label"])), esc(verdict_name(b["label"])), a["label"] != b["label"]),
        tile(t("ab.accusatory"), str(a["n_bad"]), str(b["n_bad"]), a["n_bad"] != b["n_bad"]),
        tile(t("ab.data_shared"), esc(t(f'scope.{a["scope"]}')), esc(t(f'scope.{b["scope"]}')), a["scope"] != b["scope"]),
        tile(t("ab.rules"), str(len(a["rules"])), str(len(b["rules"])), len(a["rules"]) != len(b["rules"])),
        tile(t("ab.gaps"), str(len(a["gaps"])), str(len(b["gaps"])), set(a["gaps"]) != set(b["gaps"])),
    ]
    st.markdown(f'<div style="display:flex;gap:.7rem;flex-wrap:wrap;margin:.4rem 0 1rem 0">{"".join(tiles)}</div>',
                unsafe_allow_html=True)


def column(letter: str, me: dict, other: dict) -> None:
    color = COL_A if letter == "A" else COL_B
    head = t("ab.col_a") if letter == "A" else t("ab.col_b")
    st.markdown(f'<div class="vd-colhead" style="background:{color}">{esc(head)}</div>', unsafe_allow_html=True)
    with st.container(border=True):
        # verdict
        conf = f' · {t("verdict.confidence")}: <b>{float(me["conf"]):.0%}</b>' if me["conf"] is not None else ""
        box = verdict_box(me["label"], conf.lstrip(" ·"), big=True)
        if me["label"] != other["label"]:
            box = f'<div style="outline:4px solid #ffe066;border-radius:14px">{box}</div>'
        st.markdown(box, unsafe_allow_html=True)
        # wording
        st.markdown(f'#### {t("ab.wording_title")}')
        html_text, n_bad = mark_accusatory(me["text"])
        st.markdown(f'<div class="vd-text" style="font-size:1.15rem">{html_text or "—"}</div>', unsafe_allow_html=True)
        if n_bad:
            st.markdown(f'<span class="vd-ko">✘ {n_bad} {esc(t("ab.accusatory"))}</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="vd-ok">✔ {esc(t("ab.no_accusation"))}</span>', unsafe_allow_html=True)
        # data shared
        st.markdown(f'#### {t("ab.data_shared")}')
        s = me["scope"]
        line = hl(badge(t(f"scope.{s}"), SCOPE_COLOR.get(s, GREY)), s != other["scope"])
        if me["records"] is not None:
            line += hl(chip(f'{me["records"]} {t("shared.records")}'), me["records"] != other["records"])
        st.markdown(line, unsafe_allow_html=True)
        if me["fields"]:
            parts = []
            for k in me["fields"]:
                only_me = k not in other["fields"]
                pii = k in PII_FIELDS
                style = "background:#fdeaea;border-color:#e8a0a0;color:#8a0000" if pii else ""
                parts.append(f'<span class="vd-chip" style="{style}{";outline:2px solid #ffe066" if only_me else ""}">{esc(k)}</span>')
            st.markdown(" ".join(parts), unsafe_allow_html=True)
        # rules
        st.markdown(f'#### {t("ab.rules")} ({len(me["rules"])})')
        if me["rules"]:
            for rid, f in me["rules"].items():
                is_new = rid not in other["rules"]
                st.markdown(hl(badge(rid, ACTION_COLOR.get(f.get("action"), GREY)), is_new) +
                            f' <span style="font-size:.9rem">{esc(pick(f.get("message_fr"), f.get("message_en")))[:170]}</span>',
                            unsafe_allow_html=True)
        else:
            st.caption(t("ab.no_rules"))
        # gaps
        st.markdown(f'#### {t("ab.gaps")} ({len(me["gaps"])})')
        if me["gaps"]:
            for gid, g in me["gaps"].items():
                st.markdown(hl(badge(gid, "#7a3db8"), gid not in other["gaps"]) + f' {esc(g.get("title"))}', unsafe_allow_html=True)
        else:
            st.caption(t("ab.no_gaps"))
        # oversight
        st.markdown(f'#### {t("ab.oversight")}')
        o = [(t("flag.registrar"), me["registrar"], other["registrar"]), (t("flag.holder_notified"), me["holder"], other["holder"]),
             (t("flag.transfer"), me["transfer"], other["transfer"]), (t("flag.blocked"), me["blocked"], other["blocked"])]
        st.markdown("<br>".join(hl(f'{yes_no(v)} {esc(lbl)}', v != w) for lbl, v, w in o), unsafe_allow_html=True)
        rv = me["review"]
        if rv:
            msg = f'{t("ab.review")}: <b>{esc(rv.get("status"))}</b>'
            if rv.get("supervisor_alert"):
                msg += f' · <span class="vd-ko">{esc(t("ab.supervisor_alert"))}</span>'
            st.markdown(hl(msg, True), unsafe_allow_html=True)
        if me["chain"] is not None:
            st.caption(f'{t("ab.audit")}: {me["audit_n"]} {t("audit.events")} · {t("audit.chain")} {"✔" if me["chain"] else "✘"}')


def diff_table(run_out: dict) -> None:
    rows = run_out.get("diff") or []
    if not rows:
        return
    body = ""
    for r in rows:
        if r.get("field") == "text":
            continue
        cls = "diff" if r.get("differs") else ""
        body += (f'<tr class="{cls}"><td><b>{esc(r.get("field"))}</b></td><td>{esc(r.get("A"))}</td>'
                 f'<td>{esc(r.get("B"))}</td><td>{"≠" if r.get("differs") else "="}</td></tr>')
    st.markdown(f'<table class="vd-table"><tr><th>{esc(t("ab.diff_field"))}</th><th>A</th><th>B</th><th></th></tr>{body}</table>',
                unsafe_allow_html=True)


def assertions(run_out: dict) -> None:
    ass = run_out.get("assertions") or {}
    if not ass:
        return
    for letter in ("A", "B"):
        items = ass.get(letter) or []
        ok = sum(1 for x in items if x.get("ok"))
        st.markdown(f'**{letter}** — {ok}/{len(items)} {esc(t("ab.expected_met"))}')
        for x in items:
            st.markdown(f'{yes_no(x.get("ok"))} `{x.get("name")}` <span style="color:#777">{esc(x.get("detail"))}</span>',
                        unsafe_allow_html=True)


def show_scenario(sid: str, out: dict) -> None:
    a, b = summary(out.get("A") or {}), summary(out.get("B") or {})
    headline(a, b)
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        column("A", a, b)
    with c2:
        column("B", b, a)
    with st.expander(t("ab.diff_title")):
        diff_table(out)
    if out.get("assertions"):
        with st.expander(t("ab.assertions")):
            assertions(out)
    if out.get("scenario_gaps"):
        with st.expander(t("ab.scenario_gaps")):
            for g in out["scenario_gaps"]:
                st.markdown(badge(str(g.get("gap_id") or "?"), "#7a3db8") + f' {esc(g.get("title"))}', unsafe_allow_html=True)


# ---------------------------------------------------------------------------------------------------- page
lst = api("GET", "/scenarios", timeout=10)
scenarios = (lst.data or {}).get("scenarios") if lst.ok and isinstance(lst.data, dict) else None
if not scenarios:
    if not lst.ok:
        warn_api(lst, t("ab.list"))
    scenarios = DEFAULT_SCENARIOS
by_id = {s["id"]: s for s in scenarios}
ids = list(by_id)

results = st.session_state.setdefault("ab_results", {})


def run_one(sid: str) -> bool:
    r = api("POST", f"/scenarios/{sid}/run", timeout=240)
    if not r.ok or not isinstance(r.data, dict):
        warn_api(r, f"{t('ab.run')} {sid}")
        if r.status in (422, 500):
            st.caption(api_error_text(r))
        return False
    results[sid] = r.data
    return True


def sc_title(sid: str) -> str:
    s = by_id.get(sid, {})
    return f'{sid} · {pick(s.get("title_fr"), s.get("title_en")) or ""}'


sid = st.radio(t("ab.pick"), ids, horizontal=True, key="ab_sid")
meta = by_id.get(sid, {})
st.markdown(f'<div style="font-size:1.35rem;font-weight:700;margin:.3rem 0">{esc(sc_title(sid))}</div>', unsafe_allow_html=True)
if meta.get("description"):
    st.caption(meta["description"])

b1, b2, _ = st.columns([1, 1, 2])
if b1.button(t("ab.run_one", sid=sid), type="primary", **STRETCH):
    with st.spinner(t("ab.running", sid=sid)):
        run_one(sid)
if b2.button(t("ab.run_all"), **STRETCH):
    bar = st.progress(0.0, text=t("ab.running", sid=""))
    for i, s_id in enumerate(ids):
        bar.progress(i / len(ids), text=t("ab.running", sid=s_id))
        if not run_one(s_id):
            break
    bar.progress(1.0, text=t("ab.done"))

if sid in results:
    show_scenario(sid, results[sid])
else:
    st.info(t("ab.empty"))

if results:
    st.divider()
    st.subheader(t("ab.summary"))
    rows = ""
    for s_id in ids:
        if s_id not in results:
            continue
        a, b = summary(results[s_id].get("A") or {}), summary(results[s_id].get("B") or {})
        passed = results[s_id].get("passed")
        sa, sb = t("scope." + a["scope"]), t("scope." + b["scope"])
        rows += (f'<tr><td><b>{esc(s_id)}</b> {esc(pick(by_id.get(s_id, {}).get("title_fr"), by_id.get(s_id, {}).get("title_en")) or "")}</td>'
                 f'<td>{badge(verdict_name(a["label"]), "#a8321f")}</td><td>{badge(verdict_name(b["label"]), "#1b6f8a")}</td>'
                 f'<td>{esc(sa)} → {esc(sb)}</td>'
                 f'<td>{a["n_bad"]} → {b["n_bad"]}</td><td>{len(a["rules"])} → {len(b["rules"])}</td>'
                 f'<td>{esc(", ".join(b["gaps"]) or "—")}</td>'
                 f'<td>{yes_no(bool(passed)) if passed is not None else "—"}</td></tr>')
    head = "".join(f"<th>{esc(x)}</th>" for x in (t("ab.col_scenario"), "A", "B", t("ab.data_shared"), t("ab.accusatory"),
                                                  t("ab.rules"), t("ab.gaps"), t("ab.expected_met")))
    st.markdown(f'<table class="vd-table"><tr>{head}</tr>{rows}</table>', unsafe_allow_html=True)
