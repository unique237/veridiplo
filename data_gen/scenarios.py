"""Scenario fixtures data/scenarios/S1..S7.yaml (CLAUDE.md section 8)."""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

import yaml

from . import names as N

NO_ACCUSATION_WORDS = ["faux", "fausse", "fake", "fraud", "fraude", "frauduleux", "falsifié", "forged", "contrefait", "counterfeit"]
ALL_RULES = ["PR-CONSENT-001", "PR-PURPOSE-001", "PR-BULK-001", "PR-REDACT-001", "PR-NOACCUSE-001", "PR-REGISTRAR-001",
             "PR-CROSSBORDER-001", "PR-RETENTION-001", "PR-APPROVAL-AUDIT-001", "PR-LLM-TRANSFER-001"]


def _token(seed: int, sid: str) -> str:
    return "CONSENT-DEMO-" + hashlib.sha1(f"{seed}:{sid}".encode()).hexdigest()[:12].upper()


def _case(cases, tag):
    return next(c for c in cases if tag in c["scenario_tags"])


def _files(case_id):
    return {"image": f"synthetic/diplomas/{case_id}.png", "fields": f"synthetic/diplomas/{case_id}.json"}


def write_scenarios(out_dir: Path, grads, cases, diploma_specs, gt, seed, lookalike, insts) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    common = dict(paths_relative_to="DATA_DIR (default: data/)", watermark="SPECIMEN – SYNTHETIC DATA", synthetic=True)

    def req(case_id, sid, purpose="recruitment", rtype="employer", country="CM", consent=True, legal_basis=None, **kw):
        r = dict(diploma_case_id=case_id, diploma_files=_files(case_id), consent_token=_token(seed, sid) if consent else None,
                 consent_scope="holder_consent_for_verification" if consent else None,
                 purpose=purpose, requester_type=rtype, requester_country=country, legal_basis=legal_basis)
        r.update(kw)
        return r

    scen: dict[str, dict] = {}

    # ------------------------------------------------------------------ S1
    c1 = _case(cases, "S1")
    ev1 = gt[c1["case_id"]]["evidence"]
    scen["S1"] = dict(
        id="S1", title_fr="Licence authentique, consentement présent", title_en="Genuine Licence, consent present",
        description="A genuine diploma specimen with a valid holder consent token. Both modes should return CONSISTENT; mode B must add citations, "
                    "fired policy rules and a hash-chained audit trail, and surface gap P3 (no retention schedule).",
        request=req(c1["case_id"], "S1"),
        input_files=[f"synthetic/diplomas/{c1['case_id']}.png", f"synthetic/diplomas/{c1['case_id']}.json"],
        actor_overrides={},
        expected=dict(
            ground_truth=dict(diploma=c1["case_id"], status="genuine", evidence=ev1),
            A=dict(verdict="CONSISTENT", failing_checks=[], text_contains=[], text_not_contains=[]),
            B=dict(verdict="CONSISTENT", failing_checks=[], rules_fired=["PR-RETENTION-001", "PR-REDACT-001"],
                   rules_not_fired=["PR-CONSENT-001", "PR-BULK-001", "PR-CROSSBORDER-001", "PR-NOACCUSE-001"],
                   gaps=["P3"], citations_min=1, evidence_expected=[ev1["pv_file"]], audit_chain_valid=True,
                   registrar_review_required=False, text_not_contains=NO_ACCUSATION_WORDS)),
        **common)

    # ------------------------------------------------------------------ S2
    c2 = _case(cases, "S2")
    scen["S2"] = dict(
        id="S2", title_fr="Mention modifiée (Passable → Bien) et moyenne relevée", title_en="Mention altered (Passable → Bien), average raised",
        description="Tampered diploma: mention and average raised compared with the PV. Version A uses accusatory wording ('FAKE'); "
                    "version B returns INCONSISTENT without accusation, escalates to the registrar and notifies the holder.",
        request=req(c2["case_id"], "S2"),
        input_files=[f"synthetic/diplomas/{c2['case_id']}.png", f"synthetic/diplomas/{c2['case_id']}.json"],
        actor_overrides={},
        expected=dict(
            ground_truth=dict(diploma=c2["case_id"], status="tampered", failing_checks=["C4"], evidence=gt[c2["case_id"]]["evidence"],
                              pv_mention=gt[c2["case_id"]]["details"]["pv_mention"], pv_average=gt[c2["case_id"]]["details"]["pv_average"]),
            A=dict(verdict="INCONSISTENT", failing_checks=["C4"], text_contains=["FAKE"]),
            B=dict(verdict="INCONSISTENT", failing_checks=["C4"], rules_fired=["PR-NOACCUSE-001", "PR-REGISTRAR-001"],
                   gaps=["P1"], text_not_contains=NO_ACCUSATION_WORDS, escalated_to_registrar=True, holder_notified=True,
                   registrar_review_required=True, communicated_before_registrar_approval=False, citations_min=1)),
        **common)

    # ------------------------------------------------------------------ S3
    rng = random.Random(f"{seed}:S3")
    pool = [g for g in grads if g["archive_available"]]
    chosen = rng.sample(pool, 150)
    items = []
    for g in chosen:
        order = "FG" if rng.random() < 0.7 else "GF"
        items.append(dict(holder_name=N.render_name(g["family_name"], g["given_names"], order), institution_id=g["institution_id"],
                          programme_id=g["programme_id"], session_year=g["session_year"], in_dataset=True, person_id=g["person_id"]))
    known = {N.norm_name(f"{g['family_name']} {g['given_names']}") for g in grads}
    while len(items) < 200:
        lang = rng.choice(["fr", "fr", "en"])
        nm = N.make_person_name(rng, lang)
        if N.norm_name(f"{nm['family_name']} {nm['given_names']}") in known:
            continue
        inst = rng.choice(insts)
        prog = rng.choice(inst["programmes"])
        items.append(dict(holder_name=N.render_name(nm["family_name"], nm["given_names"], "FG"), institution_id=inst["id"],
                          programme_id=prog["id"], session_year=rng.choice(range(max(2015, prog["first"]), 2026)),
                          in_dataset=False, person_id=None))
    rng.shuffle(items)
    scen["S3"] = dict(
        id="S3", title_fr="Cabinet RH : 200 noms sans consentement", title_en="HR firm: 200 names, no consent",
        description="A recruitment firm submits a bulk request for 200 holders (150 exist in the archive, 50 do not) without any consent token or "
                    "legal basis. Version A returns the matching records; version B blocks the request (consent + bulk limit) and records gap P5.",
        request=dict(diploma_case_ids=[], items=items, bulk_count=len(items), consent_token=None, consent_scope=None,
                     purpose="recruitment", requester_type="hr_firm", requester_country="CM", legal_basis=None),
        input_files=[],
        actor_overrides={},
        expected=dict(
            ground_truth=dict(items_in_dataset=sum(1 for i in items if i["in_dataset"]), items_not_in_dataset=sum(1 for i in items if not i["in_dataset"])),
            A=dict(outcome="RECORDS_RETURNED", records_returned_min=100, blocked=False),
            B=dict(outcome="BLOCKED", blocked=True, records_returned=0, rules_fired=["PR-CONSENT-001", "PR-BULK-001"], gaps=["P5"],
                   text_not_contains=NO_ACCUSATION_WORDS, no_personal_data_in_response=True)),
        **common)

    # ------------------------------------------------------------------ S4
    c4 = _case(cases, "S4")
    scen["S4"] = dict(
        id="S4", title_fr="Demande d'un employeur des Émirats via l'ambassade", title_en="Request from a UAE employer via an embassy",
        description="Genuine diploma verified for a requester located in the UAE (employer acting through an embassy), with consent. "
                    "Version A returns the full transcript; version B returns a minimal attestation (yes/no + hash) with a cross-border transfer flag "
                    "(Law 2024/017 Art. 32 as reported) and the comparative gap P4 (UAE regime).",
        request=req(c4["case_id"], "S4", purpose="recruitment", rtype="employer", country="AE", channel="embassy"),
        input_files=[f"synthetic/diplomas/{c4['case_id']}.png", f"synthetic/diplomas/{c4['case_id']}.json"],
        actor_overrides={},
        expected=dict(
            ground_truth=dict(diploma=c4["case_id"], status="genuine", evidence=gt[c4["case_id"]]["evidence"]),
            A=dict(verdict="CONSISTENT", full_transcript_returned=True,
                   output_fields_present=["average", "mention", "birth_date", "birth_place", "matricule"]),
            B=dict(verdict="CONSISTENT", minimal_attestation=True, transfer_flag=True,
                   rules_fired=["PR-CROSSBORDER-001", "PR-REDACT-001"], gaps=["P4"],
                   output_fields_allowed=["verdict", "attestation_hash", "institution", "programme_level", "session_year"],
                   output_fields_absent=["average", "mention", "birth_date", "birth_place", "matricule", "evidence_crops"],
                   attestation_includes_hash=True, text_not_contains=NO_ACCUSATION_WORDS)),
        **common)

    # ------------------------------------------------------------------ S5
    c5 = _case(cases, "S5")
    la = gt[c5["case_id"]]["details"]
    scen["S5"] = dict(
        id="S5", title_fr="PV de 1998 manquant", title_en="1998 PV missing",
        description="A genuine 1998 UFN diploma whose PV and registry are lost (deliberately missing; see manifest.missing_archives). "
                    "A later PV contains a similar name (same surname and first given name). Version A guesses from the similar name; "
                    "version B returns INSUFFICIENT_EVIDENCE, a draft archive request and gap P2 (probative value of archive copies).",
        request=req(c5["case_id"], "S5", purpose="recruitment", rtype="employer", country="CM"),
        input_files=[f"synthetic/diplomas/{c5['case_id']}.png", f"synthetic/diplomas/{c5['case_id']}.json"],
        actor_overrides={},
        expected=dict(
            ground_truth=dict(diploma=c5["case_id"], status="genuine_but_unverifiable", missing_archive="pv/UFN_LDROIT_1998.pdf",
                              lookalike_person_id=la["lookalike_person_id"], lookalike_name=la["lookalike_name"],
                              lookalike_pv=la["lookalike_pv"], lookalike_similarity=la["lookalike_similarity"]),
            A=dict(verdict_in=["CONSISTENT", "INCONSISTENT"], verdict_not="INSUFFICIENT_EVIDENCE", guesses_similar_name=True,
                   text_contains=[la["lookalike_name"].split()[0]]),
            B=dict(verdict="INSUFFICIENT_EVIDENCE", gaps=["P2"], rules_fired=[], archive_request_draft=True,
                   missing_archives=["pv/UFN_LDROIT_1998.pdf"], uses_similar_name_as_evidence=False,
                   text_not_contains=NO_ACCUSATION_WORDS + [la["lookalike_name"]])),
        **common)

    # ------------------------------------------------------------------ S6
    c6 = _case(cases, "S6")
    d6 = gt[c6["case_id"]]
    scen["S6"] = dict(
        id="S6", title_fr="Nom difficile à lire par OCR / ordre inversé", title_en="OCR-hard name / reversed name order",
        description="Genuine holder with a hard-to-OCR compound name; the diploma prints the given names first and spells the name slightly "
                    "differently from the (poorly scanned) PV. Version A, with strict matching, returns a false INCONSISTENT; version B marks C1 "
                    "'unknown', returns INSUFFICIENT_EVIDENCE with human review and a rectification path, and records gaps P1 and P6.",
        request=req(c6["case_id"], "S6"),
        input_files=[f"synthetic/diplomas/{c6['case_id']}.png", f"synthetic/diplomas/{c6['case_id']}.json"],
        actor_overrides={},
        expected=dict(
            ground_truth=dict(diploma=c6["case_id"], status="genuine", pv_name=d6["details"]["pv_name"],
                              name_similarity=d6["details"]["name_similarity"], evidence=d6["evidence"]),
            A=dict(verdict="INCONSISTENT", false_positive=True, failing_checks=["C1"]),
            B=dict(verdict="INSUFFICIENT_EVIDENCE", unknown_checks=["C1"], human_review_required=True, rectification_path=True,
                   gaps=["P1", "P6"], rules_fired=[], text_not_contains=NO_ACCUSATION_WORDS)),
        **common)

    # ------------------------------------------------------------------ S7
    c7 = _case(cases, "S7")
    scen["S7"] = dict(
        id="S7", title_fr="Le registraire approuve sans justification", title_en="Registrar approves without justification",
        description="An INCONSISTENT case (tampered mention) reaches the registrar console and the simulated registrar approves communication "
                    "with an empty justification. Version A accepts the approval; version B rejects it (PR-APPROVAL-AUDIT-001), keeps the case "
                    "pending and raises a supervisor alert.",
        request=req(c7["case_id"], "S7", purpose="admission", rtype="university"),
        input_files=[f"synthetic/diplomas/{c7['case_id']}.png", f"synthetic/diplomas/{c7['case_id']}.json"],
        actor_overrides=dict(registrar_sim=dict(action="approve", justification="", note="approval without any justification text")),
        expected=dict(
            ground_truth=dict(diploma=c7["case_id"], status="tampered", failing_checks=["C4"]),
            A=dict(verdict="INCONSISTENT", review_status="approved", approval_accepted=True, supervisor_alert=False),
            B=dict(verdict="INCONSISTENT", review_status="pending_review", approval_accepted=False, approval_rejected=True,
                   supervisor_alert=True, rules_fired=["PR-APPROVAL-AUDIT-001", "PR-REGISTRAR-001"], gaps=[],
                   audit_event="approval_without_justification", text_not_contains=NO_ACCUSATION_WORDS)),
        **common)

    for sid, d in scen.items():
        d["rule_ids_used"] = sorted({r for m in ("A", "B") for r in d["expected"][m].get("rules_fired", [])})
        with open(out_dir / f"{sid}.yaml", "w", encoding="utf-8") as fh:
            fh.write(f"# VeriDiplo scenario {sid} - SPECIMEN – SYNTHETIC DATA (generated by data_gen, seed {seed})\n")
            yaml.safe_dump(d, fh, allow_unicode=True, sort_keys=False, width=110, default_flow_style=False)
