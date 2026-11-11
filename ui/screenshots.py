"""Headless screenshots of every VeriDiplo UI page for the submission (docs/screenshots/*.png).

Usage (stack up and seeded, `make up && make seed`):
    pip install -r ui/requirements-screenshots.txt && playwright install chromium
    python ui/screenshots.py [--base http://localhost:8501] [--out docs/screenshots] [--lang fr|en]

Env: CHROMIUM_PATH to use an existing Chromium/Chrome binary instead of the Playwright-managed one.
All content shown is SPECIMEN - SYNTHETIC DATA.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

WIDTH = 1600
T = {  # strings per language (must match ui/i18n.py)
    "fr": {"run": "Lancer la vérification", "search": "Rechercher", "run_ab": "Exécuter {sid} en A et B", "run_all": "Tout exécuter",
           "verdict": "INCONSISTANT", "ab_ready": "VERSION B", "pending": "À revoir"},
    "en": {"run": "Run verification", "search": "Search", "run_ab": "Run {sid} in A and B", "run_all": "Run all",
           "verdict": "INCONSISTENT", "ab_ready": "VERSION B", "pending": "To review"},
}


def settle(page: Page, ms: int = 600) -> None:
    """Wait for Streamlit to finish a script run."""
    try:
        page.wait_for_selector('[data-testid="stApp"]', timeout=30000)
        page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000)
    except PWTimeout:
        pass
    page.wait_for_timeout(ms)


def snap(page: Page, out: Path, name: str, min_height: int = 1000) -> None:
    """Resize the viewport to the full content height (Streamlit scrolls an inner container), then capture."""
    settle(page, 400)
    h = page.evaluate("() => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.documentElement;"
                      " return m.scrollHeight; }")
    page.set_viewport_size({"width": WIDTH, "height": max(min_height, min(int(h) + 60, 7000))})
    page.wait_for_timeout(500)
    path = out / f"{name}.png"
    page.screenshot(path=str(path))
    print("saved", path)
    page.set_viewport_size({"width": WIDTH, "height": min_height})


def goto(page: Page, base: str, slug: str, lang: str) -> None:
    page.goto(f"{base}/{slug}?lang={lang}".replace("/?", "?") if slug else f"{base}/?lang={lang}", wait_until="networkidle")
    settle(page)


def pick_option(page: Page, label: str, value: str) -> None:
    box = page.get_by_label(label, exact=False).first
    box.click()
    box.fill(value)
    page.keyboard.press("Enter")
    settle(page)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("UI_URL", "http://localhost:8501"))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "screenshots"))
    ap.add_argument("--lang", default="fr", choices=["fr", "en"])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    s = T[a.lang]
    suffix = "" if a.lang == "fr" else "_en"
    with sync_playwright() as pw:
        kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.getenv("CHROMIUM_PATH") else {}
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"], **kw)
        page = browser.new_page(viewport={"width": WIDTH, "height": 1000})
        page.set_default_timeout(60000)
        try:
            goto(page, a.base, "", a.lang)
            snap(page, out, f"01_home{suffix}")

            # 2. verify: TMEN-001 (tampered mention) in governed mode
            goto(page, a.base, "Verifier_un_diplome", a.lang)
            sample = page.locator('[data-testid="stSelectbox"]').first
            sample.click()
            page.keyboard.type("TMEN-001")
            page.keyboard.press("Enter")
            settle(page)
            page.get_by_role("button", name=s["run"]).click()
            try:
                page.wait_for_selector(f'.vd-verdict:has-text("{s["verdict"]}")', timeout=90000)
            except PWTimeout:
                print("warning: verdict not displayed in time", file=sys.stderr)
            settle(page, 1200)
            snap(page, out, f"02_verify{suffix}", 1100)
            # evidence and rules tabs
            for tab, name in (("Preuves" if a.lang == "fr" else "Evidence", "02b_verify_evidence"),
                              ("Règles appliquées" if a.lang == "fr" else "Rules fired", "02c_verify_rules")):
                try:
                    page.get_by_role("tab", name=tab).first.click()
                    settle(page, 900)
                    if name.endswith("rules"):
                        page.locator('[data-testid="stExpander"] summary').first.click()
                        settle(page, 900)
                    snap(page, out, f"{name}{suffix}", 1100)
                except PWTimeout:
                    print(f"warning: tab {tab} not found", file=sys.stderr)

            # 3. search
            goto(page, a.base, "Recherche_de_preuves", a.lang)
            page.locator('input[type="text"]').first.fill("BELINGA")
            page.get_by_role("button", name=s["search"]).click()
            settle(page, 1500)
            snap(page, out, f"03_search{suffix}")

            # 4. registrar (the verify step above queued a case)
            goto(page, a.base, "Console_du_registraire", a.lang)
            snap(page, out, f"04_registrar{suffix}")

            # 5. KB
            goto(page, a.base, "Base_de_connaissances", a.lang)
            page.locator('input[type="text"]').first.fill("consentement" if a.lang == "fr" else "consent")
            page.get_by_role("button", name=s["search"]).click()
            settle(page, 1200)
            snap(page, out, f"05_kb_search{suffix}")
            page.get_by_role("tab", name="Règles de politique" if a.lang == "fr" else "Policy rules").click()
            settle(page, 800)
            snap(page, out, f"05b_kb_rules{suffix}")

            # 6. A/B scenarios
            goto(page, a.base, "Scenarios_AB", a.lang)
            for sid in ("S2", "S3", "S4", "S7"):
                page.get_by_text(sid, exact=True).first.click()
                settle(page, 500)
                page.get_by_role("button", name=s["run_ab"].format(sid=sid)).click()
                try:
                    page.wait_for_selector(f'.vd-colhead:has-text("{s["ab_ready"]}")', timeout=120000)
                except PWTimeout:
                    print(f"warning: {sid} result not displayed", file=sys.stderr)
                settle(page, 1200)
                snap(page, out, f"06_ab_{sid}{suffix}", 1100)

            # 7. audit and gaps
            goto(page, a.base, "Audit_et_ecarts", a.lang)
            snap(page, out, f"07_audit{suffix}")
            page.get_by_role("tab", name="Registre des écarts" if a.lang == "fr" else "Gap register").click()
            settle(page, 800)
            snap(page, out, f"07b_gaps{suffix}")

            # 8. pipeline
            goto(page, a.base, "Pipeline_Y3172", a.lang)
            page.wait_for_selector("svg", timeout=20000)
            snap(page, out, f"08_pipeline{suffix}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
