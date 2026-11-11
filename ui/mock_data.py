"""Offline mock of the VeriDiplo API (UI_MOCK=1). Same shapes as app/core/schemas.py and the real routes.

All content is SPECIMEN data. KB excerpts below are placeholders marked "[MOCK EXCERPT]" - never real legal text.
"""
from __future__ import annotations

import io
import re
import time
import uuid
from typing import Any

from common import Resp

_CASES: dict[str, dict[str, Any]] = {}
T0 = time.time()
GAP_SEEDS = [
    {"id": 1, "gap_id": "P1", "type": "policy", "title": "No procedure for communicating an inconsistency to the holder",
     "detail": {"finding": "[MOCK] No text defines who tells the holder and how to contest.", "proposal": "Registrar-mediated notice with a rectification path.",
                "surfaced_by": ["S2"], "sources": ["CM-DP-2024"]}, "case_id": None, "scenario_id": None, "checkpoint": None},
    {"id": 2, "gap_id": "P2", "type": "policy", "title": "No archive-retention duty for deliberation minutes",
     "detail": {"finding": "[MOCK] Missing 1998 PV cannot be reconstructed.", "proposal": "Define minimum retention and a digitisation duty.",
                "surfaced_by": ["S5"], "sources": []}, "case_id": None, "scenario_id": None, "checkpoint": None},
    {"id": 3, "gap_id": "S1", "type": "standard", "title": "No national interoperable diploma-record standard",
     "detail": {"finding": "[MOCK] Issuers use heterogeneous serial formats.", "comparison": "W3C VC 2.0 offers verifiable credentials.",
                "surfaced_by": [], "sources": ["W3C-VC-2.0"]}, "case_id": None, "scenario_id": None, "checkpoint": None},
]
CHUNKS = {
    "CM-DP-2024#c014": {"chunk_id": "CM-DP-2024#c014", "source_id": "CM-DP-2024", "title": "Loi n° 2024/017 relative à la protection des données (extrait MOCK)",
                        "issuer": "Présidence de la République", "date": "2024-12-23", "jurisdiction": "CM", "binding": True, "kind": "primary",
                        "article": "Art. 9", "as_reported": False, "url": "https://example.org/mock-source",
                        "text": "[MOCK EXCERPT] Texte d'illustration : le traitement est subordonné au consentement préalable de la personne concernée."},
    "CM-DP-2024#c055": {"chunk_id": "CM-DP-2024#c055", "source_id": "CM-DP-2024", "title": "Loi n° 2024/017 (extrait MOCK)", "issuer": "Présidence de la République",
                        "date": "2024-12-23", "jurisdiction": "CM", "binding": True, "kind": "summary", "article": "Art. 32", "as_reported": True,
                        "url": "https://example.org/mock-secondary", "text": "[MOCK EXCERPT] Résumé d'illustration sur les transferts transfrontaliers."},
    "CM-MINESUP-FORM#c001": {"chunk_id": "CM-MINESUP-FORM#c001", "source_id": "CM-MINESUP-FORM", "title": "Formulaire d'équivalence (extrait MOCK)",
                             "issuer": "MINESUP", "date": "2023-01-01", "jurisdiction": "CM", "binding": False, "kind": "primary", "article": None,
                             "as_reported": False, "url": "https://example.org/mock-form",
                             "text": "[MOCK EXCERPT] Le demandeur autorise la recherche auprès de l'établissement."},
}
RULES = {
    "PR-CONSENT-001": ("before_search", "block", "Recherche refusée : aucun consentement du titulaire ni base légale.",
                       "Search refused: no holder consent or legal basis.", ["CM-DP-2024#c014", "CM-MINESUP-FORM#c001"], False),
    "PR-BULK-001": ("before_search", "block", "Requête en masse refusée : plus de 20 titulaires par heure.",
                    "Bulk request refused: more than 20 holders per hour.", ["CM-DP-2024#c014"], True),
    "PR-REDACT-001": ("before_report", "redact", "Données non nécessaires retirées du rapport.",
                      "Non-essential data removed from the report.", ["CM-DP-2024#c014"], False),
    "PR-NOACCUSE-001": ("before_report", "redact", "Formulation neutre : aucune accusation, renvoi au registraire.",
                        "Neutral wording: no accusation, referral to the registrar.", ["CM-DP-2024#c014"], False),
    "PR-REGISTRAR-001": ("before_share", "escalate", "Approbation du registraire obligatoire pour tout verdict INCONSISTANT.",
                         "Registrar approval is mandatory for any INCONSISTENT verdict.", ["CM-DP-2024#c014"], False),
    "PR-CROSSBORDER-001": ("before_export_abroad", "redact", "Demande transfrontalière : attestation minimale (oui/non + hachage).",
                           "Cross-border request: minimal attestation (yes/no + hash).", ["CM-DP-2024#c055"], False),
    "PR-APPROVAL-AUDIT-001": ("before_share", "block", "Approbation sans justification refusée ; alerte au superviseur.",
                              "Approval without justification refused; supervisor alerted.", ["CM-DP-2024#c014"], True),
}
NODES = [("MLFO", "Orchestrator", "plan"), ("C", "IntakeAgent", "intake"), ("P", "PolicyGuardianAgent", "checkpoint:before_ingest"),
         ("PP", "ExtractionAgent", "extract_fields"), ("P", "PolicyGuardianAgent", "checkpoint:before_search"),
         ("M", "EvidenceRetrievalAgent", "retrieve"), ("M", "ConsistencyAgent", "check"),
         ("D", "ReporterAgent", "report"), ("P", "PolicyGuardianAgent", "checkpoint:before_report"), ("SINK", "Orchestrator", "release")]


# ------------------------------------------------------------------------------------------------ builders
def _rule(rid: str) -> dict:
    cp, action, fr, en, chunks, prop = RULES[rid]
    return {"rule_id": rid, "checkpoint": cp, "action": action, "message_fr": fr, "message_en": en, "kb_chunk_ids": chunks, "proposal": prop}


def _gap(gid: str, cid: str, title: str, cp: str, reason: str, proposal: str = "") -> dict:
    return {"gap_id": gid, "type": "policy", "title": title, "checkpoint": cp,
            "detail": {"kind": "no_rule", "reason": reason, "case_id": cid, "proposal": proposal}}


def _checks(kind: str) -> list[dict]:
    def c(i: str, f: str, st: str, ev: list[str], fr: str, en: str, crit: bool = True, score: Any = 1.0) -> dict:
        return {"check_id": i, "field": f, "status": st, "evidence_ids": ev, "detail": fr, "detail_en": en, "critical": crit, "score": score}
    if kind == "MISS":
        un = ("unknown", [], "Aucune pièce d'archive trouvée.", "No archive record found.", True, None)
        return [c("C1", "holder_name", *un), c("C2", "birth_date", "unknown", [], "Non comparable.", "Not comparable.", False, None),
                c("C3", "programme_id", "pass", ["E1"], "Programme accrédité.", "Programme accredited."),
                c("C4", "average/mention", *un), c("C5", "session_year", *un),
                c("C6", "signatory_name", "pass", ["E2"], "Signataire en fonction.", "Signatory in office."),
                c("C7", "serial", "pass", ["E3"], "Format valide.", "Valid format.", True, 0.6)]
    if kind == "OCRH":
        return [c("C1", "holder_name", "unknown", ["E1"], "Nom proche (score 88) : revue humaine.", "Close name (score 88): human review.", True, 0.88),
                c("C3", "programme_id", "pass", ["E3"], "Programme accrédité.", "Programme accredited."),
                c("C4", "average/mention", "pass", ["E1"], "Moyenne et mention concordent.", "Average and mention match.")]
    fail = kind == "TAMP"
    return [c("C1", "holder_name", "pass", ["E1"], "Nom concordant (score 100).", "Name matches (score 100)."),
            c("C2", "birth_date", "pass", ["E1"], "Naissance concordante.", "Birth data match.", False),
            c("C3", "programme_id", "pass", ["E3"], "Programme accrédité sur la session.", "Programme accredited for the session."),
            c("C4", "average/mention", "fail" if fail else "pass", ["E1"],
               "Mention « Bien » sur le diplôme, « Passable » sur le PV (moyenne 10,53)." if fail else "Moyenne et mention concordent avec le PV.",
               "Mention 'Bien' on the diploma, 'Passable' on the PV (average 10.53)." if fail else "Average and mention match the PV."),
            c("C5", "session_year", "pass", ["E1"], "Dates cohérentes.", "Dates coherent."),
            c("C6", "signatory_name", "pass", ["E2"], "Signataire en fonction.", "Signatory in office."),
            c("C7", "serial", "pass", ["E4"], "Numéro valide et unique.", "Serial valid and unique."),
            c("C8", "image", "unknown", [], "Contrôle d'image non exécuté.", "Image check not run.", False, None)]


def _evidence(kind: str) -> list[dict]:
    if kind == "MISS":
        return [{"evidence_id": "E1", "kind": "accreditation", "source_file": "knowledge-graph (SPARQL)", "snippet": "Accréditation LDROIT@UFN : en cours",
                 "supports": ["programme_id"], "contradicts": [], "score": 1.0, "fields": {}},
                {"evidence_id": "E2", "kind": "signatory", "source_file": "knowledge-graph (SPARQL)", "snippet": "Signataire en fonction",
                 "supports": ["signatory_name"], "contradicts": [], "score": 1.0, "fields": {}},
                {"evidence_id": "E3", "kind": "kg_fact", "source_file": "knowledge-graph (SPARQL)", "snippet": "Format de numéro UFN",
                 "supports": ["serial"], "contradicts": [], "score": 1.0, "fields": {}}]
    tamp = kind == "TAMP"
    return [
        {"evidence_id": "E1", "kind": "pv_row", "source_file": "pv/UFN_LDROIT_2024.pdf", "sha256": "ab12" * 16, "page": 2, "row_idx": 5,
         "bbox": [60.0, 310.0, 540.0, 332.0], "page_size": [595.0, 842.0], "chunk_id": 1,
         "snippet": "5 BELINGA Bertrand 1999-05-25 10,53 Passable", "fields": {"average": 10.53, "mention": "Passable"}, "score": 0.97,
         "supports": ["holder_name", "birth_date"], "contradicts": ["average", "mention"] if tamp else [], "crop_url": "/search/crop/1.png", "ocr_conf": 93.0},
        {"evidence_id": "E2", "kind": "signatory", "source_file": "knowledge-graph (SPARQL)", "snippet": "Signataire Joséphine ABENA MANGA : 2022-10-01 -> en cours",
         "supports": ["signatory_name"], "contradicts": [], "score": 1.0, "fields": {}},
        {"evidence_id": "E3", "kind": "accreditation", "source_file": "knowledge-graph (SPARQL)", "snippet": "Accréditation LDROIT@UFN : 1992-10-01 -> en cours",
         "supports": ["programme_id"], "contradicts": [], "score": 1.0, "fields": {}},
        {"evidence_id": "E4", "kind": "registry_row", "source_file": "registries/UFN_2024.csv", "page": 1, "row_idx": 153,
         "bbox": [40.0, 120.0, 560.0, 140.0], "page_size": [595.0, 842.0], "chunk_id": 2,
         "snippet": "UFN/2024/LDROIT/00153 BELINGA Bertrand Passable", "fields": {}, "score": 0.9, "supports": ["serial"], "contradicts": [],
         "crop_url": "/search/crop/2.png"}]


def _kind(req: dict) -> str:
    cid = ((req.get("diploma_case_id") or "") + str((req.get("fields") or {}).get("holder_name", ""))).upper()
    if cid.startswith(("TMEN", "TAVG", "TNAM")):
        return "TAMP"
    if cid.startswith("MISS"):
        return "MISS"
    if cid.startswith("OCRH"):
        return "OCRH"
    return "GEN"


def build_result(cid: str, mode: str, req: dict, review: dict | None = None) -> dict:
    kind = _kind(req)
    bulk = bool(req.get("items")) or int(req.get("batch_size") or 1) > 1
    abroad = (req.get("requester_country") or "CM") != "CM"
    consent = bool(req.get("consent_token") or req.get("legal_basis"))
    res: dict[str, Any] = {"case_id": cid, "policy_mode": mode, "status": "done", "verdict": None, "checks": [], "evidence": [],
                           "decisions": [], "fired_rules": [], "gaps": [], "explanation_fr": "", "explanation_en": "",
                           "shared_output": {}, "needs_registrar": False, "holder_notified": False, "archive_request_draft": None,
                           "report_sha256": "9f" * 32, "blocked": False, "transfer_flag": False}
    on = mode == "on"
    if bulk:
        if on and not consent:
            res.update(status="blocked", blocked=True, fired_rules=[_rule("PR-CONSENT-001"), _rule("PR-BULK-001")],
                       gaps=[_gap("P5", cid, "Bulk verification by HR firms has no legal framework", "before_search",
                                  "Bulk query without consent or legal basis.", "Define accredited bulk verifiers.")],
                       explanation_fr="Recherche refusée : aucun consentement du titulaire ni base légale. Requête en masse limitée à 20 titulaires par heure.",
                       explanation_en="Search refused: no holder consent or legal basis. Bulk requests limited to 20 holders per hour.")
            res["shared_output"] = {"scope": "none", "status": "blocked", "explanation_fr": res["explanation_fr"],
                                    "explanation_en": res["explanation_en"], "rule_ids": ["PR-CONSENT-001", "PR-BULK-001"]}
        else:
            res["shared_output"] = {"scope": "bulk_records", "records_returned": 150, "records": [{"holder_name": "…", "average": 12.1}],
                                    "explanation_fr": "150 enregistrements retournés.", "explanation_en": "150 records returned."}
            res.update(explanation_fr="150 enregistrements retournés.", explanation_en="150 records returned.")
        res["steps"] = []
        return _with_steps(res)
    checks, evidence = _checks(kind), _evidence(kind)
    res["checks"], res["evidence"] = checks, evidence
    if kind == "TAMP":
        label, conf = "INCONSISTENT", 0.88
        rf = ["La mention et la moyenne du diplôme diffèrent de la ligne du PV citée en preuve."]
        re_ = ["Mention and average on the diploma differ from the PV row cited as evidence."]
    elif kind == "MISS":
        label, conf = "INSUFFICIENT_EVIDENCE", 0.34
        rf = ["Aucune pièce d'archive (PV ou registre) retrouvée."]
        re_ = ["No archive record (PV or registry) found."]
    elif kind == "OCRH":
        label, conf = "INSUFFICIENT_EVIDENCE", 0.61
        rf = ["Nom proche mais incertain : revue humaine recommandée."]
        re_ = ["Close but uncertain name: human review recommended."]
    else:
        label, conf = "CONSISTENT", 0.94
        rf = ["Tous les contrôles critiques concordent avec les pièces d'archive."]
        re_ = ["All critical checks match the archive records."]
    if not on:
        if kind == "OCRH":
            label, conf = "INCONSISTENT", 0.5
            rf, re_ = ["Nom différent du PV."], ["Name differs from the PV."]
        if kind == "MISS":
            label, conf = "CONSISTENT", 0.55
            rf, re_ = ["Titulaire similaire trouvé : BELINGA Laure."], ["Similar holder found: BELINGA Laure."]
    res["verdict"] = {"label": label, "confidence": conf, "reasons_fr": rf, "reasons_en": re_, "needs_human": on and kind == "OCRH"}
    if on:
        decisions = [{"checkpoint": "before_ingest", "action": "allow", "fired": [], "gaps": ["P3"]},
                     {"checkpoint": "before_search", "action": "allow", "fired": [], "gaps": []}]
        fired = [_rule("PR-REDACT-001")]
        res["gaps"] = [_gap("P3", cid, "No published retention schedule for uploads", "before_ingest",
                            "No rule governs checkpoint 'before_ingest' although personal data is processed.",
                            "Default 90 days then deletion (OUR proposal, not law).")]
        if label == "INCONSISTENT":
            fired += [_rule("PR-NOACCUSE-001"), _rule("PR-REGISTRAR-001")]
            res["needs_registrar"], res["holder_notified"], res["status"] = True, True, "pending_review"
            res["gaps"].append(_gap("P1", cid, "No procedure to notify the holder of an inconsistency", "before_share",
                                    "No policy defines holder notification and contestation.", "Registrar-mediated notice."))
            res["explanation_fr"] = ("Des écarts ont été constatés entre le diplôme présenté et les archives (mention, moyenne). "
                                     "Aucune conclusion sur l'authenticité n'est tirée ; le dossier est transmis au registraire.")
            res["explanation_en"] = ("Discrepancies were found between the diploma and the archives (mention, average). "
                                     "No conclusion on authenticity is drawn; the case is referred to the registrar.")
            res["shared_output"] = {"scope": "none", "status": "pending_review", "explanation_fr": "Résultat en attente de revue du registraire.",
                                    "explanation_en": "Result pending registrar review."}
        elif label == "INSUFFICIENT_EVIDENCE":
            res["explanation_fr"] = "Preuves insuffisantes pour conclure. Une demande d'archives est proposée à l'établissement."
            res["explanation_en"] = "Insufficient evidence to conclude. An archive request is proposed to the institution."
            if kind == "MISS":
                res["archive_request_draft"] = "Objet : demande de recherche d'archives, PV 1998, Licence en Droit.\n[MOCK]"
                res["gaps"].append(_gap("P2", cid, "No archive-retention duty", "before_search", "PV 1998 absent.", "Retention duty."))
            res["shared_output"] = {"scope": "evidence_pack", "verdict": label, "audience": "verifier",
                                    "evidence_refs": [{"evidence_id": e["evidence_id"], "source_file": e["source_file"]} for e in evidence],
                                    "explanation_fr": res["explanation_fr"], "explanation_en": res["explanation_en"]}
        else:
            res["explanation_fr"] = "Les informations du diplôme sont cohérentes avec les sources d'archive citées."
            res["explanation_en"] = "The diploma information is consistent with the cited archive sources."
            res["shared_output"] = {"scope": "evidence_pack", "verdict": label, "audience": "verifier", "programme": "Licence en Droit Privé",
                                    "institution": "Université Fictive de Nkolbisson", "session_year": 2024,
                                    "evidence_refs": [{"evidence_id": e["evidence_id"], "source_file": e["source_file"], "page": e.get("page")}
                                                      for e in evidence],
                                    "explanation_fr": res["explanation_fr"], "explanation_en": res["explanation_en"]}
            if abroad:
                res["fired_rules"].append(_rule("PR-CROSSBORDER-001"))
                res["transfer_flag"] = True
                res["shared_output"] = {"scope": "attestation", "consistent": True, "attestation_hash": "c0ffee" * 10, "transfer_flag": True,
                                        "explanation_fr": "Attestation minimale : oui, cohérent (hachage joint).",
                                        "explanation_en": "Minimal attestation: yes, consistent (hash attached)."}
                fired.append(_rule("PR-CROSSBORDER-001"))
        res["fired_rules"] = fired
        decisions.append({"checkpoint": "before_report", "action": "redact", "fired": fired, "gaps": []})
        res["decisions"] = decisions
    else:
        if label == "INCONSISTENT":
            res["explanation_fr"] = "ATTENTION : ce diplôme est un FAUX. La mention a été falsifiée (FAKE)."
            res["explanation_en"] = "WARNING: this diploma is a FAKE. The mention has been forged."
        elif kind == "MISS":
            res["explanation_fr"] = "Diplôme valide : un titulaire au nom proche (BELINGA Laure) a été trouvé."
            res["explanation_en"] = "Valid diploma: a holder with a similar name (BELINGA Laure) was found."
        else:
            res["explanation_fr"] = "Diplôme valide."
            res["explanation_en"] = "Valid diploma."
        rec = {"holder_name": "BELINGA Bertrand", "birth_date": "1999-05-25", "birth_place": "Obala", "average": 10.53, "mention": "Passable"}
        res["shared_output"] = {"scope": "full_record", "audience": "verifier", "verdict": label, "record": rec, "average": 10.53,
                                "mention": "Passable", "birth_date": "1999-05-25", "birth_place": "Obala", "matricule": "UFN/2024/LDROIT/00153",
                                "evidence": evidence, "evidence_crops": ["/search/crop/1.png"],
                                "explanation_fr": res["explanation_fr"], "explanation_en": res["explanation_en"]}
    return _with_steps(res)


def _with_steps(res: dict) -> dict:
    res["steps"] = [{"node": n, "agent": a, "action": ac, "ms": 3 + 7 * i, "ts": None,
                     "summary": f"{ac}: mock step {i + 1} ({res['policy_mode']})"} for i, (n, a, ac) in enumerate(NODES)]
    return res


# ------------------------------------------------------------------------------------------------ images / pdf
def _png(label: str, bbox: bool = True) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (520, 130), "#f6f3ea")
    d = ImageDraw.Draw(im)
    for y in range(10, 120, 24):
        d.text((14, y), "N°  NOM PRENOM          NAISSANCE   MOYENNE   MENTION", fill="#999")
    if bbox:
        d.rectangle([6, 52, 512, 76], outline="#e53935", width=3)
    d.text((14, 58), label, fill="#222")
    d.text((300, 112), "SPECIMEN – SYNTHETIC DATA", fill="#c62828")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


MINI_PDF = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")


# ------------------------------------------------------------------------------------------------ router
def _ok(data: Any = None, content: bytes = b"", text: str = "") -> Resp:
    return Resp(True, 200, data, content, text)


def _err(status: int, msg: str = "") -> Resp:
    return Resp(False, status, {"detail": msg or "mock error"}, error=f"HTTP {status}")


def _scenario_run(sid: str) -> Resp:
    spec = {"S1": ("GEN-001", {}), "S2": ("TMEN-001", {}), "S3": (None, {"items": [{}] * 200, "batch_size": 200, "purpose": "recruitment"}),
            "S4": ("GEN-002", {"requester_country": "AE"}), "S5": ("MISS-001", {}), "S6": ("OCRH-001", {}), "S7": ("TMEN-002", {})}
    if sid not in spec:
        return _err(404, "unknown scenario")
    dcid, extra = spec[sid]
    req = {"diploma_case_id": dcid, "consent_token": None if sid == "S3" else "CONSENT-DEMO-1", **extra}
    runs = {}
    for letter, mode in (("A", "off"), ("B", "on")):
        cid = str(uuid.uuid4())
        res = build_result(cid, mode, req)
        review = None
        if sid == "S7":
            if mode == "off":
                review = {"status": "approved", "approval_accepted": True, "supervisor_alert": False}
            else:
                review = {"status": "pending_review", "approval_rejected": True, "supervisor_alert": True}
        runs[letter] = {"mode": mode, "label": letter, "case_id": cid, "status": res["status"], "result": res, "review": review,
                        "audit_chain_valid": True, "audit_n": 14 if mode == "on" else 6}
    diff = []
    for k, f in (("verdict", lambda r: (r["verdict"] or {}).get("label")), ("blocked", lambda r: r["blocked"]),
                 ("needs_registrar", lambda r: r["needs_registrar"]), ("output_scope", lambda r: r["shared_output"].get("scope")),
                 ("rules_fired", lambda r: sorted(x["rule_id"] for x in r["fired_rules"])),
                 ("gaps", lambda r: sorted(g["gap_id"] for g in r["gaps"]))):
        a, b = f(runs["A"]["result"]), f(runs["B"]["result"])
        diff.append({"field": k, "A": a, "B": b, "differs": a != b})
    titles = {s["id"]: s for s in _scenarios()}
    return _ok({"scenario": titles[sid], "A": runs["A"], "B": runs["B"], "diff": diff, "passed": True,
                "assertions": {"A": [{"name": "verdict", "ok": True, "detail": "mock"}], "B": [{"name": "verdict", "ok": True, "detail": "mock"}]},
                "scenario_gaps": []})


def _scenarios() -> list[dict]:
    t = [("Licence authentique, consentement présent", "Genuine Licence, consent present"),
         ("Mention modifiée (Passable → Bien), moyenne relevée", "Mention altered (Passable → Bien), average raised"),
         ("Cabinet RH : 200 noms sans consentement", "HR firm: 200 names, no consent"),
         ("Demande d'un employeur des Émirats via l'ambassade", "Request from a UAE employer via an embassy"),
         ("PV de 1998 manquant", "1998 PV missing"),
         ("Nom difficile à lire (OCR) ou ordre inversé", "OCR-hard or reversed name order"),
         ("Le registraire approuve sans justification", "Registrar approves without justification")]
    return [{"id": f"S{i + 1}", "title_fr": a, "title_en": b, "description": f"[MOCK] {b}."} for i, (a, b) in enumerate(t)]


def handle(method: str, path: str, params: dict, json: Any, data: dict, files: dict) -> Resp:
    """Route a UI request to the mock backend (never raises)."""
    m = re.match
    if path == "/health":
        return _ok({"status": "ok", "db": "ok", "kg": "ok", "policy_mode": "on", "llm_provider": "none", "routers": ["mock"]})
    if path == "/cases/samples":
        return _err(404)
    if method == "POST" and path == "/cases":
        body = dict(json or {})
        if data:
            body.update({k: v for k, v in data.items() if k != "fields"})
            if data.get("fields"):
                import json as _j
                body["fields"] = _j.loads(data["fields"])
        cid = str(uuid.uuid4())
        _CASES[cid] = {"req": body, "created": time.time(), "mode": None, "started": None, "review": None, "review_state": None,
                       "has_file": bool(files)}
        return _ok({"case_id": cid, "status": "created"})
    g = m(r"^/cases/([^/]+)/run$", path)
    if method == "POST" and g:
        c = _CASES.get(g.group(1))
        if not c:
            return _err(404, "case not found")
        c["mode"], c["started"] = params.get("policy_mode") or "on", time.time()
        c["result"] = build_result(g.group(1), c["mode"], c["req"])
        if str(params.get("wait", "true")).lower() == "true":
            c["started"] -= 60
            return _ok(c["result"])
        return _ok({"case_id": g.group(1), "status": "running"})
    g = m(r"^/cases/([^/]+)/review$", path)
    if method == "POST" and g:
        c = _CASES.get(g.group(1))
        if not c or not c.get("result"):
            return _err(404, "case not found or not run yet")
        just = ((json or {}).get("justification") or "").strip()
        if c["mode"] == "on" and len(just) < 20:
            c["review"] = {"status": "pending_review", "approval_rejected": True, "supervisor_alert": True}
            return Resp(False, 422, {"accepted": False, "review": c["review"],
                                     "message_fr": "Décision rejetée : justification écrite obligatoire ; alerte envoyée au superviseur.",
                                     "message_en": "Decision rejected: a written justification is mandatory; supervisor alerted."},
                        error="HTTP 422")
        state = "approved" if json["decision"] == "approve" else "rejected"
        c["review_state"] = state
        c["review"] = {"status": state, "decision": json["decision"], "reviewer": json.get("reviewer"), "justification": just, "ts": "now"}
        c["result"]["status"] = "reviewed"
        return _ok({"accepted": True, "case_status": "reviewed", "review": c["review"]})
    g = m(r"^/cases/([^/]+)/report\.pdf$", path)
    if g:
        return _ok(content=MINI_PDF) if g.group(1) in _CASES else _err(404)
    g = m(r"^/cases/([^/]+)$", path)
    if g and method == "GET":
        c = _CASES.get(g.group(1))
        if not c:
            return _err(404, "case not found")
        if not c.get("result"):
            return _ok({"case_id": g.group(1), "status": "created", "steps": [], "request": c["req"]})
        res = dict(c["result"])
        n = int((time.time() - c["started"]) / 0.35) + 1
        steps = res["steps"]
        out = {**res, "steps": steps[:n]}
        if n < len(steps):
            out.update(status="running", verdict=None, checks=[], evidence=[], explanation_fr="", explanation_en="", shared_output={})
        out.update(review_state=c["review_state"], review=c["review"], request=c["req"],
                   diploma_fields={"holder_name": "BELINGA Bertrand", "institution_id": "UFN", "programme_id": "LDROIT",
                                   "session_year": 2024, "average": 14.27, "mention": "Bien", "serial": "UFN/2024/LDROIT/00153",
                                   "signatory_name": "Joséphine ABENA MANGA"})
        if c["review_state"]:
            out["status"] = "reviewed"
        return _ok(out)
    if path == "/cases" and method == "GET":
        want = params.get("review_state")
        out = []
        for cid, c in _CASES.items():
            if not c.get("result"):
                continue
            rs = c["review_state"] or ("pending" if c["result"]["needs_registrar"] else None)
            if want and rs != want:
                continue
            out.append({"case_id": cid, "status": c["result"]["status"], "verdict": (c["result"]["verdict"] or {}).get("label"),
                        "policy_mode": c["mode"], "review_state": rs, "scenario_id": None, "created_at": "now",
                        "holder": "BELINGA Bertrand", "institution_id": "UFN"})
        return _ok(out)
    if path == "/search/filters":
        return _ok({"institutions": ["UFB", "UFN"], "years": [2022, 2023, 2024], "programmes": ["LDROIT", "LINFO"],
                    "programmes_by_institution": {"UFN": ["LDROIT", "LINFO"], "UFB": ["LGEST"]}})
    if path == "/search":
        q = params.get("q", "")
        res = [{"chunk_id": i + 1, "kind": "pv_row", "snippet": f"{i + 1} BELINGA Bertrand 1999-05-25 10,53 Passable (match '{q}')",
                "page": 2, "row_idx": 5 + i, "bbox": [60, 310, 540, 332], "score": 0.03 - i * 0.004,
                "score_breakdown": {"fts": 0.4, "vector": 0.8, "trgm": 0.9, "rrf": 0.03 - i * 0.004}, "fields": {"average": 10.53},
                "institution_id": "UFN", "programme_id": "LDROIT", "session_year": 2024, "source_file": "UFN_LDROIT_2024.pdf",
                "crop_url": f"/search/crop/{i + 1}.png"} for i in range(3)] if q else []
        return _ok({"query": q, "count": len(res), "took_ms": 12.3, "results": res})
    g = m(r"^/search/crop/(\d+)\.png$", path)
    if g:
        return _ok(content=_png("BELINGA Bertrand   1999-05-25   10,53   Passable"))
    if path == "/kb/search":
        return _ok({"q": params.get("q"), "count": len(CHUNKS), "results": [{**c, "score": 0.5} for c in CHUNKS.values()]})
    if path == "/kb/rules":
        rules = [{"id": rid, "checkpoint": v[0], "when": "request.consent_token is None", "action": v[1], "message_fr": v[2], "message_en": v[3],
                  "justified_chunks": v[4], "pipeline_node": "P", "readiness_dims": [10], "proposal": v[5], "gap_id": None, "kind": "legal_interpretation"}
                 for rid, v in RULES.items()]
        return _ok({"count": len(rules), "rules": rules})
    g = m(r"^/kb/chunks/(.+)$", path)
    if g:
        c = CHUNKS.get(g.group(1))
        return _ok(c) if c else _err(404)
    if path == "/kb/sources":
        return _ok({"count": 2, "sources": [
            {"id": "CM-DP-2024", "title": "Loi n° 2024/017", "issuer": "Présidence", "date": "2024-12-23", "binding": True, "kind": "primary",
             "url": "https://example.org/mock", "status": "ok", "chunks": 12},
            {"id": "CM-MINESUP-FORM", "title": "Formulaire d'équivalence", "issuer": "MINESUP", "date": "2023-01-01", "binding": False,
             "kind": "primary", "url": "https://example.org/mock2", "status": "pending_download", "chunks": 0}]})
    if path == "/gaps":
        rt = []
        for cid, c in _CASES.items():
            for gp in (c.get("result") or {}).get("gaps", []):
                rt.append({"id": 100 + len(rt), "gap_id": gp["gap_id"], "type": "policy", "title": gp["title"], "detail": gp["detail"],
                           "case_id": cid, "scenario_id": None, "checkpoint": gp.get("checkpoint")})
        allg = GAP_SEEDS + rt
        return _ok({"count": len(allg), "gaps": allg})
    if path == "/gaps/export.md":
        return _ok(content=b"# VeriDiplo - Gap register (mock)\n", text="# VeriDiplo - Gap register (mock)\n")
    if path == "/audit/verify":
        return _ok({"chain_valid": True, "first_bad_id": None, "events_total": 156, "head_hash": "699e363c488109615db9503e3ef448f337a3b415"})
    g = m(r"^/audit/([^/]+)$", path)
    if g:
        evs = [{"id": i, "ts": "2026-11-11T05:39:4%d+00:00" % (i % 10), "case_id": g.group(1), "agent": a, "action": ac, "actor": "agent",
                "policy_mode": "on", "policy_rule_ids": [], "kb_chunk_ids": [], "row_hash": f"{i:02x}" * 32, "prev_hash": f"{i - 1:02x}" * 32}
               for i, (_, a, ac) in enumerate(NODES)]
        return _ok({"case_id": g.group(1), "chain_valid": True, "first_bad_id": None, "count": len(evs), "events": evs})
    if path == "/scenarios":
        return _ok({"scenarios": _scenarios()})
    g = m(r"^/scenarios/(S\d)/run$", path)
    if g and method == "POST":
        return _scenario_run(g.group(1))
    return _err(404, f"mock: no route {method} {path}")
