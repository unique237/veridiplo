"""Page 7 - ITU-T Y.3172 pipeline: agents mapped to nodes, with live counters from the API."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from common import NODE_COLORS, STRETCH, api, esc, node_chip, setup_page  # noqa: E402
from i18n import t  # noqa: E402

setup_page("page.pipeline.title", "page.pipeline.sub")

NODES = [  # (node, agents, label key)
    ("SRC", ["Orchestrator (intent)", "PV scans, registries, diploma upload"], "node.SRC"),
    ("C", ["IntakeAgent"], "node.C"),
    ("PP", ["OCRAgent", "ExtractionAgent"], "node.PP"),
    ("M", ["EvidenceRetrievalAgent", "ConsistencyAgent"], "node.M"),
    ("D", ["ReporterAgent"], "node.D"),
    ("SINK", ["Registrar console", "Verifier dashboard", "Holder portal", "Simulated actors (Registrar, Verifier, Holder)"], "node.SINK"),
    ("P", ["PolicyGuardianAgent", "GapAnalystAgent"], "node.P"),
    ("MLFO", ["Orchestrator (MLFO)"], "node.MLFO"),
]


def dot() -> str:
    def lab(n: str, agents: list[str]) -> str:
        return f'<<b>{n}</b><br/><font point-size="13">{t("node." + n)}</font><br/>' + "<br/>".join(
            f'<font point-size="12">{a}</font>' for a in agents[:3]) + ">"
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; nodesep=0.5; ranksep=0.6;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=18, fontcolor="white", margin="0.25,0.15"];',
             'edge [color="#5f6b7a", penwidth=1.6];']
    for n, agents, _ in NODES:
        lines.append(f'{n} [label={lab(n, agents)}, fillcolor="{NODE_COLORS[n]}"];')
    lines.append("SRC -> C -> PP -> M -> D -> SINK;")
    lines.append('MLFO -> SRC [style=dashed, label=" intent / plan "]; ')
    lines.append('P -> C [style=dashed, color="#b3243b"]; P -> PP [style=dashed, color="#b3243b"]; '
                 'P -> M [style=dashed, color="#b3243b"]; P -> D [style=dashed, color="#b3243b"]; '
                 'P -> SINK [style=dashed, color="#b3243b", label=" governs every node "];')
    lines.append("{rank=same; MLFO; P}")
    lines.append("}")
    return "\n".join(lines)


st.graphviz_chart(dot(), **STRETCH)

st.subheader(t("pipeline.counters"))
h = api("GET", "/health", timeout=5)
hd = h.data if isinstance(h.data, dict) else {}
gaps = api("GET", "/gaps", timeout=10)
aud = api("GET", "/audit/verify", timeout=10)
cases = api("GET", "/cases", params={"limit": 100}, timeout=10)
rules = api("GET", "/kb/rules", timeout=10)
srcs = api("GET", "/kb/sources", timeout=10)
cl = cases.data if cases.ok and isinstance(cases.data, list) else []


def n(r, key: str, default="—"):
    return (r.data or {}).get(key, default) if r.ok and isinstance(r.data, dict) else default


kpis = [
    (t("pipeline.k_cases"), len(cl) if cases.ok else "—"),
    (t("pipeline.k_inconsistent"), sum(1 for c in cl if c.get("verdict") == "INCONSISTENT") if cases.ok else "—"),
    (t("pipeline.k_pending"), sum(1 for c in cl if c.get("review_state") == "pending") if cases.ok else "—"),
    (t("pipeline.k_events"), n(aud, "events_total")),
    (t("pipeline.k_gaps"), n(gaps, "count")),
    (t("pipeline.k_rules"), n(rules, "count")),
    (t("pipeline.k_sources"), n(srcs, "count")),
]
cols = st.columns(len(kpis))
for c, (label, val) in zip(cols, kpis):
    c.markdown(f'<div class="vd-kpi"><div class="n">{esc(val)}</div><div class="l">{esc(label)}</div></div>', unsafe_allow_html=True)
st.caption(f'API: {hd.get("status", "?")} · db {hd.get("db", "?")} · kg {hd.get("kg", "?")} · policy_mode={hd.get("policy_mode", "?")}'
           f' · llm={hd.get("llm_provider", "?")} · {t("audit.chain")} '
           f'{"✔" if aud.ok and (aud.data or {}).get("chain_valid") else "?"}')

st.subheader(t("pipeline.table"))
rows = "".join(f'<tr><td>{node_chip(nd)}</td><td>{esc(t("node." + nd))}</td><td>{"<br>".join(esc(a) for a in ag)}</td></tr>'
               for nd, ag, _ in NODES)
head = "".join(f"<th>{esc(x)}</th>" for x in (t("pipeline.col_node"), t("pipeline.col_function"), t("pipeline.col_agents")))
st.markdown(f'<table class="vd-table"><tr>{head}</tr>{rows}</table>', unsafe_allow_html=True)
