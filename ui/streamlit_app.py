"""VeriDiplo Streamlit entrypoint: landing page. Other pages live in ui/pages/ (SPECIMEN - SYNTHETIC DATA)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from common import GREEN, AMBER, RED, GREY, api, badge, esc, setup_page, verdict_box  # noqa: E402
from i18n import init_lang, t  # noqa: E402

init_lang()

def home() -> None:
    setup_page("page.home.title", "page.home.sub")

    st.markdown(f'<div class="vd-text" style="font-size:1.1rem">{esc(t("home.question"))}</div>', unsafe_allow_html=True)

    st.subheader(t("home.verdicts"))
    cols = st.columns(3)
    for col, lab in zip(cols, ("CONSISTENT", "INCONSISTENT", "INSUFFICIENT_EVIDENCE")):
        col.markdown(verdict_box(lab, esc(t(f"home.verdict_help.{lab}"))), unsafe_allow_html=True)
    st.caption(t("home.never_fake"))

    st.subheader(t("home.pages"))
    PAGES = [("pages/1_Verifier_un_diplome.py", "page.verify.title", "home.card.verify"),
             ("pages/2_Recherche_de_preuves.py", "page.search.title", "home.card.search"),
             ("pages/3_Console_du_registraire.py", "page.registrar.title", "home.card.registrar"),
             ("pages/4_Base_de_connaissances.py", "page.kb.title", "home.card.kb"),
             ("pages/5_Scenarios_AB.py", "page.ab.title", "home.card.ab"),
             ("pages/6_Audit_et_ecarts.py", "page.audit.title", "home.card.audit"),
             ("pages/7_Pipeline_Y3172.py", "page.pipeline.title", "home.card.pipeline")]
    cols = st.columns(3)
    for i, (path, title, desc) in enumerate(PAGES):
        with cols[i % 3]:
            with st.container(border=True):
                try:
                    st.page_link(path, label=t(title))
                except Exception:  # page_link needs a registered page; never break the landing page
                    st.markdown(f"**{t(title)}**")
                st.caption(t(desc))

    st.subheader(t("home.status"))
    h = api("GET", "/health", timeout=4)
    hd = h.data if isinstance(h.data, dict) else {}
    if h.status == 0:
        st.warning(t("err.unreachable", api="API_URL", what="/health"))
    else:
        st.markdown(badge("API " + str(hd.get("status", h.status)), GREEN if hd.get("status") == "ok" else AMBER) +
                    badge("db " + str(hd.get("db")), GREEN if hd.get("db") == "ok" else RED) +
                    badge("kg " + str(hd.get("kg")), GREEN if hd.get("kg") == "ok" else RED) +
                    badge("policy_mode " + str(hd.get("policy_mode")), GREY) +
                    badge("llm " + str(hd.get("llm_provider")), GREY), unsafe_allow_html=True)


PAGES_NAV = [("pages/1_Verifier_un_diplome.py", "page.verify.title", "Verifier_un_diplome"),
             ("pages/2_Recherche_de_preuves.py", "page.search.title", "Recherche_de_preuves"),
             ("pages/3_Console_du_registraire.py", "page.registrar.title", "Console_du_registraire"),
             ("pages/4_Base_de_connaissances.py", "page.kb.title", "Base_de_connaissances"),
             ("pages/5_Scenarios_AB.py", "page.ab.title", "Scenarios_AB"),
             ("pages/6_Audit_et_ecarts.py", "page.audit.title", "Audit_et_ecarts"),
             ("pages/7_Pipeline_Y3172.py", "page.pipeline.title", "Pipeline_Y3172")]

# st.navigation gives translated sidebar labels (the pages/ auto-discovery would show file names)
nav = st.navigation([st.Page(home, title=t("page.home.title"), url_path="", default=True)] +
                    [st.Page(path, title=t(key), url_path=slug) for path, key, slug in PAGES_NAV])
nav.run()
