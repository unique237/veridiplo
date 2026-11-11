"""Unit tests for the ConsistencyAgent (pure functions, in-memory evidence, no database)."""
from __future__ import annotations

import pytest

from app.agents.consistency import (ConsistencyAgent, contains_forbidden, run_checks)
from app.core.schemas import DiplomaFields, EvidenceItem

SERIAL_REGEX = r"^UFN/\d{4}/[A-Z]{3,7}/\d{5}$"


def diploma(**kw) -> DiplomaFields:
    base = dict(holder_name="DAIROU Anne-Marie", birth_date="1998-07-23", birth_place="Garoua", institution_id="UFN",
                programme_id="LINFO", session_year=2024, average=13.45, mention="Assez Bien",
                serial="UFN/2024/LINFO/00036", signatory_name="Joséphine ABENA MANGA", issue_date="2024-09-30")
    base.update(kw)
    return DiplomaFields(**base)


def pv(name="DAIROU Anne-Marie", average="13.45", mention="Assez Bien", scope="session", scoped=18, sim_year=2024,
       prog="LINFO", delib="2024-07-22", ocr=0.95, eid="E1") -> EvidenceItem:
    return EvidenceItem(evidence_id=eid, kind="pv_row", source_file="pv/UFN_LINFO_2024.pdf", page=1, row_idx=1,
                        ocr_conf=ocr, fields={"name_raw": name, "average": average, "mention": mention,
                                              "_match": {"role": "best_pv", "scope": scope, "scoped_candidates": scoped,
                                                         "session_year": sim_year, "programme_id": prog,
                                                         "deliberation_date": delib}})


def registry(name="DAIROU Anne-Marie", serial="UFN/2024/LINFO/00036", eid="E2", birth="1998-07-23", place="Garoua",
             role="registry_match") -> EvidenceItem:
    return EvidenceItem(evidence_id=eid, kind="registry_row", ocr_conf=None,
                        fields={"name_raw": name, "serial": serial, "birth_date": birth, "birth_place": place,
                                "average": "13.45", "mention": "Assez Bien", "_match": {"role": role}})


def acc(periods=None, offered=True, eid="E3") -> EvidenceItem:
    periods = periods if periods is not None else [{"from": "2019-09-01", "to": None}]
    return EvidenceItem(evidence_id=eid, kind="accreditation",
                        fields={"offered": offered, "periods": periods, "institution_id": "UFN", "programme_id": "LINFO"})


def sig(terms=None, eid="E4") -> EvidenceItem:
    terms = terms if terms is not None else [{"name": "Joséphine ABENA MANGA", "institution_id": "UFN",
                                              "from": "2022-10-01", "to": None}]
    return EvidenceItem(evidence_id=eid, kind="signatory", fields={"terms": terms, "signatory_name": "x"})


def fmt(eid="E5") -> EvidenceItem:
    return EvidenceItem(evidence_id=eid, kind="kg_fact",
                        fields={"fact": "serial_format", "regex": SERIAL_REGEX, "pattern": "UFN/{YYYY}/{PROG}/{NNNNN}"})


def genuine_evidence():
    return [pv(), registry(), acc(), sig(), fmt()]


def run(fields=None, evidence=None, mode="on"):
    checks, verdict = run_checks(fields or diploma(), evidence if evidence is not None else genuine_evidence(), mode)
    return {c.check_id: c for c in checks}, verdict


def test_genuine_is_consistent():
    c, v = run()
    assert v.label == "CONSISTENT"
    assert [c[k].status for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C7")] == ["pass"] * 7
    assert not v.needs_human
    assert 0.5 < v.confidence <= 1.0


def test_check_ids_and_fields_complete():
    c, _ = run()
    assert sorted(c) == [f"C{i}" for i in range(1, 9)]
    for chk in c.values():
        assert chk.detail and chk.detail_en
    assert c["C8"].critical is False and c["C2"].critical is False


@pytest.mark.parametrize("pv_name", ["Anne-Marie DAIROU", "dairou anne marie", "DAÏROU ANNE-MARIE", "ANNE MARIE DAIROU"])
def test_name_order_and_accents_are_ignored(pv_name):
    c, v = run(evidence=[pv(name=pv_name), registry(), acc(), sig(), fmt()])
    assert c["C1"].status == "pass"
    assert v.label == "CONSISTENT"


def test_c1_ocr_band_is_unknown_and_needs_human():
    ev = [pv(name="ETOUNDI-MBALLA OWONO Jean-Remi Eloi"), acc(), sig(), fmt()]
    f = diploma(holder_name="Jean-Remi ETOUND-MBALL OWONO")
    c, v = run(f, ev)
    assert c["C1"].status == "unknown"
    assert v.label == "INSUFFICIENT_EVIDENCE" and v.needs_human
    assert c["C4"].status == "unknown"  # nothing is compared against a row the name did not match
    assert not [x for x in c.values() if x.status == "fail"]


def test_mode_off_has_no_unknown_band_and_yields_inconsistent():
    ev = [pv(name="ETOUNDI-MBALLA OWONO Jean-Remi Eloi"), acc(), sig(), fmt()]
    f = diploma(holder_name="Jean-Remi ETOUND-MBALL OWONO")
    c, v = run(f, ev, mode="off")
    assert c["C1"].status == "fail"
    assert v.label == "INCONSISTENT"


def test_c1_low_similarity_fails_when_scoped_candidates_exist():
    c, v = run(evidence=[pv(name="HAMAN Marie"), acc(), sig(), fmt()])
    assert c["C1"].status == "fail" and c["C1"].evidence_ids == ["E1"]
    assert v.label == "INCONSISTENT"


def test_c1_low_similarity_without_scoped_candidates_is_unknown():
    c, v = run(evidence=[pv(name="HAMAN Marie", scope="institution", scoped=0)])
    assert c["C1"].status == "unknown"
    assert v.label == "INSUFFICIENT_EVIDENCE"


def test_no_evidence_is_insufficient_not_inconsistent():
    c, v = run(evidence=[acc(), sig(), fmt()])
    assert c["C1"].status == "unknown"
    assert v.label == "INSUFFICIENT_EVIDENCE" and v.needs_human
    assert any("archive" in r.lower() for r in v.reasons_en)


def test_empty_evidence_everything_unknown():
    c, v = run(evidence=[])
    assert v.label == "INSUFFICIENT_EVIDENCE"
    assert all(chk.status == "unknown" for k, chk in c.items() if k != "C7") or c["C7"].status == "unknown"


def test_c4_tampered_mention():
    c, v = run(diploma(mention="Bien"))
    assert c["C4"].status == "fail"
    assert "Bien" in c["C4"].detail
    assert v.label == "INCONSISTENT" and v.needs_human


def test_c4_tampered_average():
    c, v = run(diploma(average=14.2))
    assert c["C4"].status == "fail" and v.label == "INCONSISTENT"


def test_c4_average_tolerance():
    c, _ = run(diploma(average=13.455))
    assert c["C4"].status == "pass"


def test_c4_band_incoherence_even_if_source_agrees():
    # PV and diploma agree on the numbers but the mention does not fit the average band
    ev = [pv(average="13.45", mention="Bien"), registry(), acc(), sig(), fmt()]
    c, v = run(diploma(mention="Bien"), ev)
    assert c["C4"].status == "fail"
    assert "barème" in c["C4"].detail


def test_c4_english_mention_labels_map_to_bands():
    ev = [pv(average="14.55", mention="Good"), registry(), acc(), sig(), fmt()]
    c, _ = run(diploma(average=14.55, mention="Good"), ev)
    assert c["C4"].status == "pass"
    c, _ = run(diploma(average=14.55, mention="Fairly Good"), ev)
    assert c["C4"].status == "fail"


def test_c4_falls_back_to_registry_values_when_pv_row_unreadable():
    ev = [pv(average=None, mention=None), registry(), acc(), sig(), fmt()]
    c, _ = run(evidence=ev)
    assert c["C4"].status == "pass"
    assert "E2" in c["C4"].evidence_ids


def test_c5_wrong_session_found_in_other_session():
    ev = [pv(scope="programme", sim_year=2022, delib="2022-07-10"), registry(), acc(), sig(), fmt()]
    c, v = run(evidence=ev)
    assert c["C1"].status == "pass"
    assert c["C5"].status == "fail"
    assert v.label == "INCONSISTENT"


def test_c5_issue_before_deliberation():
    c, v = run(diploma(issue_date="2024-07-01", signatory_name="Joséphine ABENA MANGA"))
    assert c["C5"].status == "fail" and v.label == "INCONSISTENT"


def test_c5_without_deliberation_date_is_partial_pass():
    c, _ = run(evidence=[pv(delib=None), registry(), acc(), sig(), fmt()])
    assert c["C5"].status == "pass"
    assert "non disponible" in c["C5"].detail


def test_c3_not_accredited_at_deliberation_date():
    ev = [pv(delib="2019-07-15"), registry(), acc(periods=[{"from": "2004-09-01", "to": "2018-08-31"},
                                                          {"from": "2019-09-01", "to": None}]), sig(), fmt()]
    c, v = run(diploma(session_year=2024), ev)
    assert c["C3"].status == "fail" and v.label == "INCONSISTENT"


def test_c3_not_offered_at_institution():
    c, v = run(evidence=[pv(), registry(), acc(offered=False, periods=[]), sig(), fmt()])
    assert c["C3"].status == "fail" and v.label == "INCONSISTENT"


def test_c3_unknown_institution_is_unknown_not_fail():
    a = acc(offered=False, periods=[])
    a.fields["institution_known"] = False
    c, v = run(evidence=[pv(), registry(), a, sig(), fmt()])
    assert c["C3"].status == "unknown" and v.label == "INSUFFICIENT_EVIDENCE"


def test_c3_year_overlap_when_no_deliberation_date():
    ev = [pv(delib=None), registry(), acc(periods=[{"from": "2019-09-01", "to": None}]), sig(), fmt()]
    c, _ = run(evidence=ev)
    assert c["C3"].status == "pass"


def test_c3_unknown_without_kg():
    c, v = run(evidence=[pv(), registry(), sig(), fmt()])
    assert c["C3"].status == "unknown" and v.label == "INSUFFICIENT_EVIDENCE"


def test_c6_signatory_out_of_office():
    terms = [{"name": "Joséphine ABENA MANGA", "institution_id": "UFN", "from": "2025-01-01", "to": None}]
    c, v = run(evidence=[pv(), registry(), acc(), sig(terms), fmt()])
    assert c["C6"].status == "fail" and v.label == "INCONSISTENT"


def test_c6_unknown_signatory_or_other_institution():
    c, _ = run(evidence=[pv(), registry(), acc(), sig([]), fmt()])
    assert c["C6"].status == "fail"
    other = [{"name": "Joséphine ABENA MANGA", "institution_id": "UFB", "from": "2000-01-01", "to": None}]
    c, _ = run(evidence=[pv(), registry(), acc(), sig(other), fmt()])
    assert c["C6"].status == "fail" and "UFB" in c["C6"].detail


def test_c6_missing_data_is_unknown_never_pass():
    c, _ = run(evidence=[pv(), registry(), acc(), fmt()])
    assert c["C6"].status == "unknown"
    c, _ = run(diploma(signatory_name=None))
    assert c["C6"].status == "unknown"
    c, _ = run(diploma(issue_date=None))
    assert c["C6"].status == "unknown"


def test_c6_boundary_dates_inclusive():
    terms = [{"name": "Joséphine ABENA MANGA", "institution_id": "UFN", "from": "2024-09-30", "to": "2024-09-30"}]
    c, _ = run(evidence=[pv(), registry(), acc(), sig(terms), fmt()])
    assert c["C6"].status == "pass"


def test_c7_duplicate_serial_held_by_other_holder():
    other = registry(name="NGONO Paulette", eid="E6", role="serial_holder")
    c, v = run(evidence=genuine_evidence() + [other])
    assert c["C7"].status == "fail" and "E6" in c["C7"].evidence_ids
    assert v.label == "INCONSISTENT"


def test_c7_same_holder_twice_is_not_a_duplicate():
    c, _ = run(evidence=genuine_evidence() + [registry(eid="E6", role="serial_holder")])
    assert c["C7"].status == "pass"


def test_c7_bad_format_and_year_mismatch():
    c, v = run(diploma(serial="UFN-2024-LINFO-36"))
    assert c["C7"].status == "fail" and v.label == "INCONSISTENT"
    c, _ = run(diploma(serial="UFN/2019/LINFO/00036"), [pv(), registry(serial="UFN/2019/LINFO/00036"), acc(), sig(), fmt()])
    assert c["C7"].status == "fail"


def test_c7_unknown_without_serial():
    c, _ = run(diploma(serial=None))
    assert c["C7"].status == "unknown"


def test_c2_mismatch_is_not_decisive_but_flags_review():
    ev = [pv(), registry(birth="1990-01-01"), acc(), sig(), fmt()]
    c, v = run(evidence=ev)
    assert c["C2"].status == "fail"
    assert v.label == "CONSISTENT" and v.needs_human


def test_c2_place_variant_passes_and_missing_birth_unknown():
    c, _ = run(diploma(birth_place="GAROUA"))
    assert c["C2"].status == "pass"
    c, v = run(diploma(birth_date=None, birth_place=None))
    assert c["C2"].status == "unknown" and v.label == "CONSISTENT"


def test_c2_registry_date_formats_are_parsed():
    ev = [pv(), registry(birth="23/07/1998"), acc(), sig(), fmt()]
    c, _ = run(evidence=ev)
    assert c["C2"].status == "pass"


def test_c8_never_decisive():
    c, v = run()
    assert c["C8"].status == "unknown" and v.label == "CONSISTENT"
    _, v2 = ConsistencyAgent().check(None, diploma(), genuine_evidence(), "on", diploma_ocr_conf=0.2)
    assert v2.label == "CONSISTENT"


def test_confidence_is_weighted_pass_ratio_times_ocr():
    _, hi = run(evidence=[pv(ocr=1.0), registry(), acc(), sig(), fmt()])
    _, lo = run(evidence=[pv(ocr=0.5), registry(), acc(), sig(), fmt()])
    assert lo.confidence == pytest.approx(hi.confidence * 0.5, abs=0.002)


def test_evidence_is_annotated_with_supports_and_contradicts():
    ev = genuine_evidence()
    run(evidence=ev)
    assert "holder_name" in ev[0].supports and "average" in ev[0].supports
    ev2 = genuine_evidence()
    run(diploma(mention="Bien"), ev2)
    assert "mention" in ev2[0].contradicts


def test_agent_interface_matches_contract():
    checks, verdict = ConsistencyAgent().check(None, diploma(), genuine_evidence(), mode="on")
    assert verdict.label == "CONSISTENT" and len(checks) == 8


@pytest.mark.parametrize("case", ["genuine", "mention", "name", "missing", "dup", "ocr"])
def test_vocabulary_never_contains_forbidden_words(case):
    if case == "genuine":
        c, v = run()
    elif case == "mention":
        c, v = run(diploma(mention="Très Bien"))
    elif case == "name":
        c, v = run(evidence=[pv(name="HAMAN Marie"), acc(), sig(), fmt()])
    elif case == "missing":
        c, v = run(evidence=[])
    elif case == "dup":
        c, v = run(evidence=genuine_evidence() + [registry(name="NGONO Paulette", eid="E6")])
    else:
        c, v = run(diploma(holder_name="Jean-Remi ETOUND-MBALL OWONO"),
                   [pv(name="ETOUNDI-MBALLA OWONO Jean-Remi Eloi")])
    texts = [x.detail for x in c.values()] + [x.detail_en for x in c.values()] + v.reasons_fr + v.reasons_en
    assert v.label in ("CONSISTENT", "INCONSISTENT", "INSUFFICIENT_EVIDENCE")
    assert not contains_forbidden(*texts)


def test_single_ocr_slip_that_changes_token_order_is_never_a_fail():
    # "ENAMA" read as "ZNAMA": token-sort similarity collapses (about 73) but the name is close token by token
    f = diploma(holder_name="ENAMA MBIDA Anne-Marie")
    c, v = run(f, [pv(name="ZNAMA MBIDA Anne-Marie"), acc(), sig(), fmt()])
    assert c["C1"].status == "unknown"
    assert v.label == "INSUFFICIENT_EVIDENCE" and v.needs_human


def test_genuinely_different_name_still_fails_after_alignment_guard():
    c, _ = run(diploma(holder_name="HAMAN Marie"), [pv(name="MAIGARI Rose-Marie"), acc(), sig(), fmt()])
    assert c["C1"].status == "fail"
