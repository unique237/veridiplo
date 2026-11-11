"""UI smoke tests with Streamlit's AppTest against the offline mock API (UI_MOCK=1). No API or DB needed.

Run (host with streamlit installed):   UI_MOCK=1 python -m pytest tests/unit/test_ui_pages.py -q
Run (ui container, bind-mounted ./ui): see README of the ui/ folder or the Makefile target `test-ui`.
"""
import os
import re
from pathlib import Path

import pytest

os.environ["UI_MOCK"] = "1"
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

UI = Path(__file__).resolve().parents[2] / "ui"
PAGES = ["streamlit_app.py"] + sorted(f"pages/{p.name}" for p in (UI / "pages").glob("*.py"))


def app(page: str, lang: str = "fr") -> AppTest:
    at = AppTest.from_file(str(UI / page), default_timeout=60)
    at.session_state["_lang"] = lang
    return at


def md(at: AppTest) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def click(at: AppTest, label_part: str):
    btn = next(b for b in at.button if label_part.lower() in b.label.lower())
    btn.click()
    return at.run()


def test_i18n_parity_and_coverage():
    import sys
    sys.path.insert(0, str(UI))
    from i18n import STRINGS
    assert set(STRINGS["fr"]) == set(STRINGS["en"]), set(STRINGS["fr"]) ^ set(STRINGS["en"])
    src = "\n".join(p.read_text(encoding="utf-8") for p in list(UI.glob("*.py")) + list((UI / "pages").glob("*.py")))
    used = set(re.findall(r'\bt\("([a-z_]+(?:\.[A-Za-z_]+)+)"', src))
    assert used, "no keys found"
    assert not (used - set(STRINGS["fr"])), sorted(used - set(STRINGS["fr"]))
    # dynamic keys
    for prefix, vals in {"verdict": ["CONSISTENT", "INCONSISTENT", "INSUFFICIENT_EVIDENCE", "BLOCKED", "ATTESTATION"],
                         "scope": ["full_record", "bulk_records", "evidence_pack", "attestation", "none"],
                         "node": ["SRC", "C", "PP", "M", "D", "SINK", "P", "MLFO"],
                         "purpose": ["recruitment", "admission", "equivalence", "marketing"],
                         "rtype": ["employer", "university", "embassy", "ministry", "hr_firm"],
                         "registrar": ["approve", "reject"],
                         "field": ["holder_name", "birth_date", "birth_place", "institution_id", "programme_id", "session_year",
                                   "average", "mention", "serial", "signatory_name", "issue_date"],
                         "home.verdict_help": ["CONSISTENT", "INCONSISTENT", "INSUFFICIENT_EVIDENCE"]}.items():
        for v in vals:
            assert f"{prefix}.{v}" in STRINGS["fr"], f"{prefix}.{v}"


@pytest.mark.parametrize("lang", ["fr", "en"])
@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page, lang):
    at = app(page, lang).run()
    assert not at.exception, [e.value for e in at.exception]
    html = md(at)
    assert "SPECIMEN" in html and "VeriDiplo" in html


def test_language_toggle():
    at = app("pages/1_Verifier_un_diplome.py").run()
    assert "Vérifier un diplôme" in md(at)
    next(r for r in at.radio if r.label == "Langue / Language").set_value("en").run()
    assert not at.exception
    assert "Verify a diploma" in md(at)


def test_verify_flow_inconsistent_then_registrar():
    at = app("pages/1_Verifier_un_diplome.py").run()
    at.selectbox(key="v_sample").select("TMEN-001").run()
    assert at.text_input(key="v_holder_name").value == "BELINGA Bertrand"
    at = click(at, "Lancer la vérification")
    assert not at.exception, [e.value for e in at.exception]
    html = md(at)
    assert "INCONSISTANT" in html
    assert any("retenu" in i.value.lower() for i in at.info)  # held for the registrar
    assert "faux" not in html.lower().replace("« faux »", "")  # B wording carries no accusation


def test_verify_without_input_warns():
    at = app("pages/1_Verifier_un_diplome.py").run()
    at = click(at, "Lancer la vérification")
    assert not at.exception
    assert any("spécimen" in w.value.lower() for w in at.warning)


def test_registrar_requires_justification():
    # create a pending case through the mock, then open the console
    import sys
    sys.path.insert(0, str(UI))
    import mock_data
    cid = mock_data.handle("POST", "/cases", {}, {"diploma_case_id": "TMEN-001", "consent_token": "X"}, {}, {}).data["case_id"]
    mock_data.handle("POST", f"/cases/{cid}/run", {"policy_mode": "on", "wait": "true"}, None, {}, {})
    at = app("pages/3_Console_du_registraire.py").run()
    assert not at.exception, [e.value for e in at.exception]
    submit = next(b for b in at.button if "Enregistrer la décision" in b.label)
    assert submit.disabled  # no justification yet
    at.text_area[0].set_value("Écart confirmé : la mention du diplôme diffère de la ligne 5 du PV 2024.").run()
    submit = next(b for b in at.button if "Enregistrer la décision" in b.label)
    assert not submit.disabled
    at = click(at, "Enregistrer la décision")
    assert not at.exception, [e.value for e in at.exception]
    assert any("enregistrée" in s.value for s in at.success)


def test_scenarios_run_all_highlights_accusation():
    at = app("pages/5_Scenarios_AB.py").run()
    at = click(at, "Tout exécuter")
    assert not at.exception, [e.value for e in at.exception]
    at.radio(key="ab_sid").set_value("S2").run()
    html = md(at)
    assert 'class="vd-bad"' in html            # A uses accusatory wording, highlighted
    assert "VERSION A" in html and "VERSION B" in html
    assert "INCONSISTANT" in html
    at.radio(key="ab_sid").set_value("S3").run()
    assert "BLOQUÉ" in md(at)
    assert not at.exception


def test_degrades_gracefully_when_api_down(monkeypatch):
    """Without mock and with an unreachable API every page must warn, not crash."""
    monkeypatch.setenv("UI_MOCK", "0")
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")
    import sys
    for m in ("common", "render", "samples", "mock_data"):
        sys.modules.pop(m, None)
    try:
        for page in PAGES:
            at = app(page).run()
            assert not at.exception, (page, [e.value for e in at.exception])
    finally:
        monkeypatch.setenv("UI_MOCK", "1")
        for m in ("common", "render", "samples", "mock_data"):
            sys.modules.pop(m, None)
