"""Shared helpers for the VeriDiplo Streamlit UI: API client, language state, page chrome, badges.

The UI talks to the API only (API_URL). Set UI_MOCK=1 to run every page against ui/mock_data.py instead
(used by the unit tests and for offline design work).
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
import streamlit as st

from i18n import get_lang, init_lang, set_lang, t

API_URL = os.getenv("API_URL", "http://api:8000").rstrip("/")
MOCK = os.getenv("UI_MOCK", "").lower() in ("1", "true", "yes")

import inspect

# `width="stretch"` replaces use_container_width in recent Streamlit; keep both working.
STRETCH: dict = ({"width": "stretch"} if "width" in inspect.signature(st.button).parameters
                 else {"use_container_width": True})

GREEN, RED, AMBER, GREY, BLUE = "#1b8a4b", "#c62828", "#d98200", "#5f6b7a", "#1f5fa8"
VERDICT_STYLE = {
    "CONSISTENT": (GREEN, "#e6f4ea"),
    "INCONSISTENT": (RED, "#fdeaea"),
    "INSUFFICIENT_EVIDENCE": (AMBER, "#fff4e0"),
    "BLOCKED": (GREY, "#eceff3"),
    "RECORDS_RETURNED": (RED, "#fdeaea"),
    "ATTESTATION": (BLUE, "#e8f0fa"),
}
NODE_COLORS = {"SRC": "#7a5195", "C": "#3d6fb6", "PP": "#2a9d8f", "M": "#e9a100", "D": "#d1603d", "SINK": "#5f6b7a",
               "P": "#b3243b", "MLFO": "#222b3a"}
ACCUSATORY = ["faux", "fausse", "fake", "fraud", "fraude", "frauduleux", "falsifié", "falsifie", "forged", "contrefait",
              "counterfeit"]
PII_FIELDS = {"record", "average", "mention", "birth_date", "birth_place", "matricule", "evidence", "holder_name"}


# ---------------------------------------------------------------------------------------------------- API client
@dataclass
class Resp:
    ok: bool
    status: int = 0
    data: Any = None
    content: bytes = b""
    text: str = ""
    error: str = ""
    headers: dict = field(default_factory=dict)

    def json_or(self, default: Any) -> Any:
        return self.data if self.ok and self.data is not None else default


def api(method: str, path: str, params: Optional[dict] = None, json: Any = None, data: Optional[dict] = None,
        files: Optional[dict] = None, timeout: float = 20) -> Resp:
    """Call the API; never raises. Returns Resp(ok=False, ...) on network errors and HTTP >= 400."""
    if MOCK:
        import mock_data
        return mock_data.handle(method, path, params or {}, json, data or {}, files or {})
    try:
        r = requests.request(method, API_URL + path, params=params, json=json, data=data, files=files, timeout=timeout)
    except requests.RequestException as exc:
        return Resp(False, 0, error=f"{type(exc).__name__}: {exc}"[:200])
    parsed = None
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        try:
            parsed = r.json()
        except ValueError:
            parsed = None
    return Resp(r.status_code < 400, r.status_code, parsed, r.content, r.text if "json" in ctype or "text" in ctype else "",
                "" if r.status_code < 400 else f"HTTP {r.status_code}", dict(r.headers))


def api_error_text(r: Resp) -> str:
    if isinstance(r.data, dict):
        d = r.data.get("detail") or r.data.get("message_fr" if get_lang() == "fr" else "message_en")
        if d:
            return str(d)
    return r.error or f"HTTP {r.status}"


def warn_api(r: Resp, what: str = "") -> None:
    """Friendly warning instead of a stack trace."""
    if r.status == 0:
        st.warning(t("err.unreachable", api=API_URL, what=what))
    elif r.status in (404, 501):
        st.warning(t("err.not_ready", what=what or t("err.endpoint")))
    elif r.status == 503:
        st.warning(t("err.unavailable", what=what or t("err.endpoint")))
    else:
        st.warning(t("err.generic", what=what, detail=api_error_text(r)))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_bytes(path: str) -> Optional[bytes]:
    """Image / PDF bytes from the API (None on failure). Cached."""
    r = api("GET", path, timeout=30)
    return r.content if r.ok and r.content else None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_chunk(chunk_id: str) -> Optional[dict]:
    r = api("GET", f"/kb/chunks/{chunk_id}")
    return r.data if r.ok and isinstance(r.data, dict) else None


# ---------------------------------------------------------------------------------------------------- chrome
CSS = """
<style>
.block-container {padding-top: 3.2rem; padding-bottom: 5rem; max-width: 1500px;}
.vd-banner-top {background: repeating-linear-gradient(135deg,#7a1020,#7a1020 14px,#8c1426 14px,#8c1426 28px);
  color:#fff; text-align:center; font-weight:700; letter-spacing:.08em; padding:.35rem .5rem; border-radius:6px;
  font-size:.85rem; margin-bottom:.6rem;}
.vd-banner-fixed {position:fixed; left:0; right:0; bottom:0; z-index:99999; background:#7a1020; color:#fff;
  text-align:center; font-weight:700; letter-spacing:.1em; font-size:.8rem; padding:.3rem 0; opacity:.96;
  pointer-events:none;}
.vd-title {font-size:2.0rem; font-weight:800; margin:0 0 .1rem 0; color:#14213d;}
.vd-sub {color:#5f6b7a; margin-bottom:.8rem;}
.vd-badge {display:inline-block; border-radius:999px; padding:.15rem .75rem; font-weight:700; font-size:.82rem;
  color:#fff; margin-right:.3rem; white-space:nowrap;}
.vd-chip {display:inline-block; border-radius:6px; padding:.05rem .5rem; font-size:.75rem; font-weight:600;
  border:1px solid #cfd6df; background:#f4f6f9; color:#33415c; margin:0 .25rem .2rem 0; white-space:nowrap;}
.vd-verdict {border-radius:14px; padding:1rem 1.4rem; border-left:12px solid; margin:.4rem 0 .8rem 0;}
.vd-verdict .lbl {font-size:2.1rem; font-weight:800; line-height:1.1;}
.vd-verdict .sub {font-size:1rem; margin-top:.25rem; color:#333;}
.vd-verdict.big .lbl {font-size:2.6rem;}
.vd-verdict.big .sub {font-size:1.2rem;}
.vd-step {display:flex; gap:.6rem; align-items:flex-start; padding:.35rem .2rem; border-bottom:1px dashed #e3e7ee;}
.node {display:inline-block; min-width:3.6rem; text-align:center; color:#fff; border-radius:6px; font-size:.72rem; font-weight:700;
  padding:.1rem .3rem; margin-top:.1rem;}
.vd-step .txt {flex:1; font-size:.9rem;}
.vd-step .ms {color:#8792a2; font-size:.75rem; white-space:nowrap;}
table.vd-table {width:100%; border-collapse:collapse; font-size:.9rem;}
table.vd-table th {background:#14213d; color:#fff; text-align:left; padding:.4rem .6rem;}
table.vd-table td {padding:.4rem .6rem; border-bottom:1px solid #e3e7ee; vertical-align:top;}
table.vd-table tr.diff td {background:#fffbe6;}
.vd-pass {color:#1b8a4b; font-weight:700;} .vd-fail {color:#c62828; font-weight:700;} .vd-unk {color:#d98200; font-weight:700;}
.vd-card {border:1px solid #dde2ea; border-radius:10px; padding:.7rem .9rem; margin-bottom:.7rem; background:#fff;}
.vd-colhead {border-radius:10px 10px 0 0; padding:.6rem 1rem; color:#fff; font-weight:800; font-size:1.25rem;}
.vd-col {border:2px solid; border-radius:0 0 10px 10px; padding:.8rem 1rem 1rem 1rem; min-height:20rem;}
.vd-col h4 {margin:.9rem 0 .3rem 0; font-size:.95rem; text-transform:uppercase; letter-spacing:.05em; color:#5f6b7a;}
.vd-text {font-size:1.05rem; line-height:1.5; background:#f8f9fb; border-radius:8px; padding:.6rem .8rem;}
mark.vd-bad {background:#ffc9c9; color:#8a0000; font-weight:800; padding:0 .15rem; border-radius:3px;}
.vd-diff {background:#fff3b0; border-radius:4px; padding:0 .25rem;}
.vd-ok {color:#1b8a4b; font-weight:700;} .vd-ko {color:#c62828; font-weight:700;}
.vd-kpi {border:1px solid #dde2ea; border-radius:10px; padding:.6rem .9rem; text-align:center; background:#fff;}
.vd-kpi .n {font-size:1.9rem; font-weight:800; color:#14213d;} .vd-kpi .l {font-size:.8rem; color:#5f6b7a;}
</style>
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def setup_page(title_key: str, subtitle_key: str = "") -> None:
    """set_page_config, CSS, language toggle, API status, persistent SPECIMEN banner and page header."""
    st.set_page_config(page_title="VeriDiplo – SPECIMEN DATA", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    init_lang()
    cur = get_lang()
    with st.sidebar:
        choice = st.radio(t("lang.label"), ["fr", "en"], index=0 if cur == "fr" else 1, horizontal=True,
                          format_func=lambda c: "Français" if c == "fr" else "English")
    if choice != cur:
        set_lang(choice)
        st.rerun()
    with st.sidebar:
        _sidebar_status()
    st.markdown(f'<div class="vd-banner-top">{esc(t("banner"))}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="vd-banner-fixed">{esc(t("banner"))}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="vd-title">VeriDiplo – SPECIMEN DATA &nbsp;·&nbsp; <span style="font-weight:600">'
                f'{esc(t(title_key))}</span></div>', unsafe_allow_html=True)
    if subtitle_key:
        st.markdown(f'<div class="vd-sub">{esc(t(subtitle_key))}</div>', unsafe_allow_html=True)


def _sidebar_status() -> None:
    if MOCK:
        st.info(t("status.mock"))
        return
    r = api("GET", "/health", timeout=3)
    h = r.data if isinstance(r.data, dict) else {}
    if r.status == 0:
        st.error(t("status.down", api=API_URL))
    elif h.get("status") == "ok":
        st.success(f"API ok · db {h.get('db')} · kg {h.get('kg')}")
    else:
        st.warning(f"API {h.get('status', r.status)} · db {h.get('db')} · kg {h.get('kg')}")
    if h:
        st.caption(f"policy_mode={h.get('policy_mode')} · llm={h.get('llm_provider')}")


# ---------------------------------------------------------------------------------------------------- html bits
def verdict_name(label: Optional[str]) -> str:
    return t(f"verdict.{label}") if label else "—"


def badge(text: str, color: str) -> str:
    return f'<span class="vd-badge" style="background:{color}">{esc(text)}</span>'


def chip(text: str, title: str = "") -> str:
    return f'<span class="vd-chip" title="{esc(title)}">{esc(text)}</span>'


def verdict_badge(label: Optional[str]) -> str:
    color = VERDICT_STYLE.get(label or "", (GREY, ""))[0]
    return badge(verdict_name(label), color)


def verdict_box(label: Optional[str], sub: str = "", big: bool = False) -> str:
    color, bg = VERDICT_STYLE.get(label or "", (GREY, "#eceff3"))
    return (f'<div class="vd-verdict{" big" if big else ""}" style="border-color:{color};background:{bg}">'
            f'<div class="lbl" style="color:{color}">{esc(verdict_name(label))}</div>'
            f'<div class="sub">{sub}</div></div>')


def node_chip(node: str) -> str:
    return f'<div class="node" style="background:{NODE_COLORS.get(node, GREY)}">{esc(node)}</div>'


def mark_accusatory(text: str) -> tuple[str, int]:
    """HTML-escape `text` and wrap accusatory words in <mark>. Returns (html, count)."""
    out, n = esc(text), 0
    for w in ACCUSATORY:
        out, k = re.subn(rf"(?i)\b({re.escape(w)})\b", r'<mark class="vd-bad">\1</mark>', out)
        n += k
    return out, n


def pick(fr: Any, en: Any) -> Any:
    """Return the FR or EN variant per language, falling back to the other one if empty."""
    a, b = (fr, en) if get_lang() == "fr" else (en, fr)
    return a or b


def case_result(data: Any) -> dict:
    """Normalise GET /cases/{id}: the result is either nested under 'result' or flattened at the top level."""
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return {**data["result"], **{k: v for k, v in data.items() if k != "result" and k not in data["result"]}}
    return data if isinstance(data, dict) else {}


def short_id(cid: Optional[str]) -> str:
    return (cid or "")[:8]
