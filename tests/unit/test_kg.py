"""Unit tests for app/kg: ontology, SHACL shapes (C2-C7), mapping, canned SPARQL, KG clients.

Run: pytest tests/unit/test_kg.py
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.kg import kg_client, mapping  # noqa: E402
from app.kg.kg_client import DATA_GRAPH, ONTOLOGY_GRAPH, LocalKG, OxigraphKG  # noqa: E402
from app.kg.mapping import VD, iri  # noqa: E402

# ----------------------------------------------------------------------------- fixture data

INSTITUTION = {
    "institution_id": "UFN",
    "name": "Université Fictive de Nkolbisson",
    "name_en": "Fictive University of Nkolbisson",
    "language": "fr",
    "private": False,
    "serial_pattern": "^UFN-YYYY-[0-9]{5}$",
    "programmes": [
        {"programme_id": "UFN-LIC-INFO", "name": "Licence en Informatique", "level": "Licence",
         "accreditations": [{"from": "2012-09-01", "to": None}]},
        {"programme_id": "UFN-LIC-GC", "name": "Licence en Génie Civil", "level": "Licence",
         "accreditations": [{"from": "2019-09-01", "to": None}]},
        {"programme_id": "UFN-LIC-BIO", "name": "Licence en Biologie", "level": "Licence",
         "accreditations": [{"from": "2010-09-01", "to": "2014-08-31"},
                            {"from": "2016-09-01", "to": "2022-08-31"}]},
    ],
    "signatories": [
        {"signatory_id": "SIG-A", "name": "Pr Emmanuel Fouda Ondoa", "office": "Recteur",
         "in_office_from": "2010-01-01", "in_office_to": "2017-12-31"},
        {"signatory_id": "SIG-B", "name": "Pr Judith Mballa Ekani", "office": "Recteur",
         "in_office_from": "2018-01-01", "in_office_to": None},
    ],
}


def _entry(eid, name, avg, mention, year=2016, prog="UFN-LIC-INFO", doc=None, **kw):
    return {"entry_id": eid, "document_id": doc or f"PV-{prog}-{year}", "page": 3, "bbox": [50, 120.5, 540, 140],
            "matricule": f"M{eid}", "name_raw": name, "name_norm": mapping.normalise_name(name),
            "average": avg, "mention": mention, "decision": "Admis", "institution_id": "UFN",
            "programme_id": prog, "session_year": year, "ocr_conf": 0.93, **kw}


ENTRIES = [
    _entry("E-ALICE", "Ngono Alice Marie", 11.5, "Passable"),
    _entry("E-CAROLE", "Tchoumi Carole", 11.2, "Passable"),
    _entry("E-DAVID", "Essomba David", 15.0, "Bien", year=2020),
    _entry("E-ERIC", "Kamga Éric", 13.0, "Assez Bien", birth_date="1994-05-12", birth_place="Yaoundé"),
    _entry("E-FELIX", "Mbarga Félix", 17.2, "Très Bien", deliberation_date="2016-07-15"),
    _entry("E-GINA", "Nkoa Gina", 12.0, "Assez Bien"),
    _entry("E-HUGO", "Biyong Hugo", 14.5, "Bien"),
    _entry("E-IRENE", "Fotso Irène", 16.0, "Très Bien"),
]

GENUINE = {"diploma_id": "D-OK", "serial": "UFN-2016-00001", "holder_name": "Ngono Alice Marie",
           "institution_id": "UFN", "programme_id": "UFN-LIC-INFO", "session_year": 2016,
           "issue_date": "2016-11-10", "signing_date": "2016-11-10", "signatory_id": "SIG-A",
           "average": 11.5, "mention": "Passable", "verified_against": ["E-ALICE"]}

TAMPERED = {
    # mention Passable -> Bien while average stays 11.2: violates the band AND differs from the PV
    "D-MENTION": {**GENUINE, "diploma_id": "D-MENTION", "serial": "UFN-2016-00002",
                  "holder_name": "Tchoumi Carole", "average": 11.2, "mention": "Bien",
                  "verified_against": ["E-CAROLE"]},
    # average raised, mention coherent with the new average: only differs from the PV
    "D-AVERAGE": {**GENUINE, "diploma_id": "D-AVERAGE", "serial": "UFN-2016-00003",
                  "holder_name": "Nkoa Gina", "average": 14.5, "mention": "Bien",
                  "verified_against": ["E-GINA"]},
    # signed in 2016 by a rector who only took office in 2018
    "D-SIGNATORY": {**GENUINE, "diploma_id": "D-SIGNATORY", "serial": "UFN-2016-00004",
                    "holder_name": "Biyong Hugo", "average": 14.5, "mention": "Bien",
                    "signatory_id": "SIG-B", "verified_against": ["E-HUGO"]},
    # Génie Civil only accredited from 2019-09-01, diploma claims session 2016
    "D-ACCRED": {**GENUINE, "diploma_id": "D-ACCRED", "serial": "UFN-2016-00005",
                 "holder_name": "Fotso Irène", "programme_id": "UFN-LIC-GC", "average": 16.0,
                 "mention": "Très Bien", "verified_against": []},
    # serial of the genuine diploma re-used by another holder
    "D-DUPLICATE": {**GENUINE, "diploma_id": "D-DUPLICATE", "holder_name": "Essomba David", "session_year": 2016,
                    "average": 15.0, "mention": "Bien", "verified_against": []},
    # serial format invalid for the institution
    "D-FORMAT": {**GENUINE, "diploma_id": "D-FORMAT", "serial": "XX-16-1", "holder_name": "Kamga Éric",
                 "average": 13.0, "mention": "Assez Bien", "verified_against": []},
    # wrong birth date (C2) and diploma dated before the deliberation (C5)
    "D-BIRTH": {**GENUINE, "diploma_id": "D-BIRTH", "serial": "UFN-2016-00006", "holder_name": "Kamga Éric",
                "birth_date": "1993-05-12", "average": 13.0, "mention": "Assez Bien",
                "verified_against": ["E-ERIC"]},
    "D-EARLY": {**GENUINE, "diploma_id": "D-EARLY", "serial": "UFN-2016-00007", "holder_name": "Mbarga Félix",
                "issue_date": "2016-06-01", "signing_date": "2016-06-01", "average": 17.2,
                "mention": "Très Bien", "verified_against": ["E-FELIX"]},
}

GENUINE_OTHERS = {
    "D-OK-2": {**GENUINE, "diploma_id": "D-OK-2", "serial": "UFN-2020-00001", "holder_name": "Essomba David",
               "session_year": 2020, "issue_date": "2020-12-01", "signing_date": "2020-12-01",
               "signatory_id": "SIG-B", "average": 15.0, "mention": "Bien", "verified_against": ["E-DAVID"]},
    "D-OK-BIO": {**GENUINE, "diploma_id": "D-OK-BIO", "serial": "UFN-2018-00001", "holder_name": "Nobody Known",
                 "programme_id": "UFN-LIC-BIO", "session_year": 2018, "issue_date": "2018-11-01",
                 "signing_date": "2018-11-01", "signatory_id": "SIG-B", "average": 10.0, "mention": "Passable",
                 "verified_against": []},
}


def build_graph(extra_diplomas: dict | None = None, with_tampered: bool = True) -> Graph:
    g = Graph()
    mapping.institution_to_rdf(INSTITUTION, g)
    for e in ENTRIES:
        mapping.entry_to_rdf(e, g)
    diplomas = {"D-OK": GENUINE, **GENUINE_OTHERS, **(TAMPERED if with_tampered else {}), **(extra_diplomas or {})}
    for d in diplomas.values():
        mapping.diploma_to_rdf(d, g)
    return g


def flagged(report):
    """{check_id: {diploma local ids}} for diploma focus nodes."""
    out = {}
    for r in report:
        if "/diploma/" in r["focus_node"]:
            out.setdefault(r["check_id"], set()).add(r["focus_node"].rsplit("/", 1)[-1])
    return out


@pytest.fixture(scope="module")
def full_report():
    conforms, report = mapping.validate(build_graph())
    assert not conforms
    return report


# ----------------------------------------------------------------------------- ontology

def test_ontology_has_all_classes_and_key_properties():
    onto = mapping.ontology_graph()
    classes = {"Person", "Diploma", "Institution", "Programme", "ExamSession", "Deliberation", "DeliberationEntry",
               "Grade", "Mention", "Signatory", "Accreditation", "SourceDocument", "EvidenceItem",
               "VerificationCase", "Verdict", "PolicyRule", "PolicyGap", "LegalProvision"}
    owl_class = URIRef("http://www.w3.org/2002/07/owl#Class")
    defined = {str(s).rsplit("#", 1)[-1] for s in onto.subjects(RDF.type, owl_class)}
    assert classes <= defined
    props = {"holder", "issuedBy", "forProgramme", "inSession", "recordedIn", "serialNumber", "average", "mention",
             "signedBy", "inOfficeFrom", "inOfficeTo", "accreditedFrom", "accreditedTo", "sourcePage", "bbox",
             "sha256", "supports", "contradicts", "justifiedBy"}
    declared = {str(s).rsplit("#", 1)[-1] for s in onto.subjects()}
    assert props <= declared
    # FR + EN labels on every class
    for c in classes:
        langs = {l.language for l in onto.objects(VD[c], URIRef("http://www.w3.org/2000/01/rdf-schema#label"))}
        assert {"fr", "en"} <= langs, c
    assert (VD.justifiedBy, URIRef("http://www.w3.org/2002/07/owl#inverseOf"), VD.justifies) in onto


def test_mention_normalisation_fr_en():
    assert mapping.mention_iri("Très Bien") == VD.TresBien
    assert mapping.mention_iri("TRES BIEN") == VD.TresBien
    assert mapping.mention_iri("assez bien") == VD.AssezBien
    assert mapping.mention_iri("First Class Honours") == VD.TresBien
    assert mapping.mention_iri("Second Class Upper Division") == VD.Bien
    assert mapping.mention_iri("Second Class Lower") == VD.AssezBien
    assert mapping.mention_iri("Third Class") == VD.Passable
    assert mapping.mention_iri("Pass") == VD.Passable
    assert mapping.mention_iri("n'importe quoi") is None
    assert mapping.mention_iri(None) is None
    # bands [10,12) [12,14) [14,16) [16,20]
    for avg, expected in [(10, VD.Passable), (11.99, VD.Passable), (12, VD.AssezBien), (13.5, VD.AssezBien),
                          (14, VD.Bien), (15.99, VD.Bien), (16, VD.TresBien), (20, VD.TresBien), (9.5, VD.Ajourne)]:
        assert mapping.mention_for_average(avg) == expected, avg


def test_name_helpers_order_and_accents():
    assert mapping.normalise_name("Kamga-Éric  jean") == "KAMGA ERIC JEAN"
    assert mapping.name_key("Jean-Paul MBARGA") == mapping.name_key("Mbarga Jean Paul")


# ----------------------------------------------------------------------------- SHACL

def test_genuine_data_conforms():
    conforms, report = mapping.validate(build_graph(with_tampered=False))
    assert conforms, report
    assert report == []


def test_tampered_cases_flagged_exactly(full_report):
    f = flagged(full_report)
    assert f["C4"] == {"D-MENTION", "D-AVERAGE"}
    assert f["C6"] == {"D-SIGNATORY"}
    assert f["C3"] == {"D-ACCRED"}
    # the duplicated serial is flagged on both holders (genuine + duplicate); the format fault on D-FORMAT
    assert f["C7"] == {"D-OK", "D-DUPLICATE", "D-FORMAT"}
    assert f["C2"] == {"D-BIRTH"}
    assert f["C5"] == {"D-EARLY"}
    # structural shapes are all satisfied
    assert "STRUCT" not in f
    # genuine diplomas are never flagged on C3/C4/C6 and never on C2/C5
    for ok in ("D-OK", "D-OK-2", "D-OK-BIO"):
        assert all(ok not in v for k, v in f.items() if k != "C7"), (ok, f)
    assert "D-OK-2" not in f["C7"] and "D-OK-BIO" not in f["C7"]


def test_each_tamper_isolated():
    """Each tampered diploma alone (with the genuine registry) violates exactly its own checks."""
    expectations = {
        "D-MENTION": {"C4"},
        "D-AVERAGE": {"C4"},
        "D-SIGNATORY": {"C6"},
        "D-ACCRED": {"C3"},
        "D-FORMAT": {"C7"},
        "D-BIRTH": {"C2"},
        "D-EARLY": {"C5"},
        "D-DUPLICATE": {"C7"},
    }
    for did, checks in expectations.items():
        conforms, report = mapping.validate(build_graph({did: TAMPERED[did]}, with_tampered=False))
        got = {r["check_id"] for r in report if r["focus_node"].endswith("/" + did)}
        assert got == checks, (did, got, report)
        assert not conforms


def test_accreditation_gap_between_periods():
    """Biology was accredited 2010-14 and 2016-22: session 2015 is a gap, 2018 is fine."""
    bad = {**GENUINE_OTHERS["D-OK-BIO"], "diploma_id": "D-BIO-2015", "serial": "UFN-2015-00001",
           "session_year": 2015, "issue_date": "2015-11-01", "signing_date": "2015-11-01", "signatory_id": "SIG-A"}
    _, report = mapping.validate(build_graph({"D-BIO-2015": bad}, with_tampered=False))
    assert flagged(report) == {"C3": {"D-BIO-2015"}}
    # exact session date inside the second period but diploma year-only would overlap: use precise date
    y16 = {**bad, "session_year": 2016, "issue_date": "2016-11-01", "signing_date": "2016-11-01"}
    ok = {**y16, "diploma_id": "D-BIO-2016", "serial": "UFN-2016-00009", "session_date": "2016-09-02"}
    bad2 = {**y16, "diploma_id": "D-BIO-2016B", "serial": "UFN-2016-00010", "session_date": "2016-08-31"}
    _, report = mapping.validate(build_graph({"D-BIO-2016": ok, "D-BIO-2016B": bad2}, with_tampered=False))
    assert flagged(report) == {"C3": {"D-BIO-2016B"}}


def test_upper_band_inclusive_and_lower_bound():
    edge = {**GENUINE, "diploma_id": "D-EDGE", "serial": "UFN-2016-00020", "holder_name": "Edge Case",
            "average": 20.0, "mention": "Très Bien", "verified_against": []}
    edge2 = {**GENUINE, "diploma_id": "D-EDGE2", "serial": "UFN-2016-00021", "holder_name": "Edge Case Two",
             "average": 16.0, "mention": "Bien", "verified_against": []}  # 16.0 belongs to Très Bien
    _, report = mapping.validate(build_graph({"D-EDGE": edge, "D-EDGE2": edge2}, with_tampered=False))
    assert flagged(report) == {"C4": {"D-EDGE2"}}


def test_english_honours_label_accepted():
    d = {**GENUINE, "diploma_id": "D-EN", "serial": "UFN-2016-00030", "holder_name": "Honours Student",
         "average": 17.0, "mention": "First Class Honours", "verified_against": []}
    conforms, report = mapping.validate(build_graph({"D-EN": d}, with_tampered=False))
    assert conforms, report


def test_pv_entry_band_violation():
    bad_entry = _entry("E-BAD", "Pv Inconsistent", 11.0, "Très Bien")
    g = build_graph(with_tampered=False)
    mapping.entry_to_rdf(bad_entry, g)
    _, report = mapping.validate(g)
    assert [(r["check_id"], r["focus_node"].rsplit("/", 1)[-1]) for r in report] == [("C4", "E-BAD")]


def test_report_has_bilingual_messages_and_check_ids(full_report):
    c6 = [r for r in full_report if r["check_id"] == "C6"][0]
    assert c6["message_fr"].startswith("C6:") and c6["message_en"].startswith("C6:")
    assert c6["severity"] == "Violation"


def test_evidence_item_requires_sha256_page_bbox():
    good = {"evidence_id": "EV1", "document_id": "PV-1", "entry_id": "E-ALICE", "page": 3,
            "bbox": [10, 20, 300, 40], "sha256": "a" * 64, "supports": ["mention"], "check_id": "C4"}
    g = mapping.evidence_to_rdf(good)
    assert mapping.validate(g) == (True, [])

    for bad_patch, path in [({"sha256": None}, "sha256"), ({"sha256": "xyz"}, "sha256"),
                            ({"page": None}, "sourcePage"), ({"page": 0}, "sourcePage"),
                            ({"bbox": None}, "bbox"), ({"bbox": "1,2,3"}, "bbox")]:
        g = mapping.evidence_to_rdf({**good, **bad_patch})
        conforms, report = mapping.validate(g)
        assert not conforms, bad_patch
        assert all(r["path"].endswith(path) for r in report), (bad_patch, report)


def test_verdict_shape():
    ok = mapping.verdict_to_rdf({"case_id": "C1", "verdict": "INCONSISTENT", "requires_review": True})
    assert mapping.validate(ok)[0]
    no_review = mapping.verdict_to_rdf({"case_id": "C2", "verdict": "INCONSISTENT", "requires_review": False})
    assert not mapping.validate(no_review)[0]
    faux = mapping.verdict_to_rdf({"case_id": "C3", "verdict": "FAKE", "requires_review": True})
    assert not mapping.validate(faux)[0]


def test_policy_rule_links_to_legal_provision():
    rule = {"id": "PR-CONSENT-001", "checkpoint": "before_search", "action": "block",
            "message_fr": "Recherche refusée.", "message_en": "Search refused.",
            "justified_by": ["CM-DP-2024#consent"]}
    g = mapping.policy_rule_to_rdf(rule, provisions={"CM-DP-2024#consent": {"article": "Art. 32", "binding": True,
                                                                             "as_reported": True}})
    assert mapping.validate(g)[0]
    rows = LocalKG()
    rows.load_graph(g)
    res = rows.sparql_select(mapping.PREFIXES + """
        SELECT ?rule ?chunk WHERE { ?p vd:ruleId ?rule ; vd:justifiedBy ?l . ?l vd:chunkId ?chunk .
                                    ?l vd:justifies ?p }""")
    assert res == [{"rule": "PR-CONSENT-001", "chunk": "CM-DP-2024#consent"}]
    unjustified = mapping.policy_rule_to_rdf({"id": "PR-X", "action": "allow"})
    assert not mapping.validate(unjustified)[0]
    proposal = mapping.policy_rule_to_rdf({"id": "PR-RET", "action": "allow", "proposal": True})
    assert mapping.validate(proposal)[0]


# ----------------------------------------------------------------------------- mapping round-trip

def test_entry_roundtrip_contract():
    g = mapping.entry_to_rdf(ENTRIES[3])
    back = mapping.entry_from_rdf(g, iri("entry", "E-ERIC"))
    expected = {k: v for k, v in ENTRIES[3].items() if k not in ("birth_date", "birth_place")}
    assert back == {**expected, "bbox": [50.0, 120.5, 540.0, 140.0]}
    # None average / unknown mention label survive
    g = mapping.entry_to_rdf(_entry("E-X", "Anon Person", None, "Mention inconnue"))
    back = mapping.entry_from_rdf(g, iri("entry", "E-X"))
    assert back["average"] is None and back["mention"] == "Mention inconnue"
    assert (iri("entry", "E-X"), VD.mention, None) not in g


def test_diploma_roundtrip():
    g = mapping.diploma_to_rdf(GENUINE)
    back = mapping.diploma_from_rdf(g, iri("diploma", "D-OK"))
    for k in ("serial", "holder_name", "institution_id", "programme_id", "session_year", "issue_date",
              "signing_date", "signatory_id", "average", "mention", "verified_against"):
        assert back[k] == GENUINE[k], k


def test_mapping_accepts_pydantic_like_objects():
    class M:
        def model_dump(self):
            return ENTRIES[0]
    g = mapping.entry_to_rdf(M())
    assert (iri("entry", "E-ALICE"), RDF.type, VD.DeliberationEntry) in g


# ----------------------------------------------------------------------------- canned queries on a KG

@pytest.fixture()
def kg():
    k = LocalKG()
    kg_client.load_ontology(k)
    mapping.load_into_kg(build_graph(), k)
    return k


def test_find_entries_by_name(kg):
    rows = mapping.find_entries_by_name("KAMGA ERIC", institution="UFN", year=2016, kg=kg)
    assert [r["entry_id"] for r in rows] == ["E-ERIC"]
    r = rows[0]
    assert r["average"] == 13.0 and r["mention"] == "Assez Bien" and r["page"] == 3
    assert r["bbox"] == "50,120.5,540,140" and r["document"].endswith("/document/PV-UFN-LIC-INFO-2016")
    # reversed order / accents / hyphens give the same hit
    assert [x["entry_id"] for x in mapping.find_entries_by_name("Éric Kamga", kg=kg)] == ["E-ERIC"]
    # filters
    assert mapping.find_entries_by_name("KAMGA ERIC", year=2015, kg=kg) == []
    assert mapping.find_entries_by_name("KAMGA ERIC", institution="XXX", kg=kg) == []
    assert mapping.find_entries_by_name("ESSOMBA DAVID", year=2020, kg=kg)[0]["entry_id"] == "E-DAVID"
    # token mode returns candidates sharing a token
    cands = mapping.find_entries_by_name("Ngono Berthe", kg=kg, mode="token")
    assert [c["entry_id"] for c in cands] == ["E-ALICE"]
    # quoting safety
    assert mapping.find_entries_by_name('X" } ; DROP', kg=kg) == []


def test_programme_accreditation(kg):
    info = mapping.programme_accreditation("UFN", "UFN-LIC-GC", session_year=2016, kg=kg)
    assert info["offered"] and info["covered"] is False
    assert mapping.programme_accreditation("UFN", "UFN-LIC-GC", session_year=2019, kg=kg)["covered"] is True
    assert mapping.programme_accreditation("UFN", "UFN-LIC-GC", at_date="2019-08-31", kg=kg)["covered"] is False
    assert mapping.programme_accreditation("UFN", "UFN-LIC-GC", at_date="2019-09-01", kg=kg)["covered"] is True
    bio = mapping.programme_accreditation("UFN", "UFN-LIC-BIO", session_year=2015, kg=kg)
    assert len(bio["accreditations"]) == 2 and bio["covered"] is False
    assert mapping.programme_accreditation("OTHER", "UFN-LIC-GC", kg=kg)["offered"] is False


def test_signatory_in_office(kg):
    assert mapping.signatory_in_office("SIG-A", "2016-11-10", kg=kg)[0]["name"] == "Pr Emmanuel Fouda Ondoa"
    assert mapping.signatory_in_office("SIG-B", "2016-11-10", kg=kg) == []
    assert mapping.signatory_in_office("SIG-B", "2030-01-01", kg=kg)[0].get("to") is None
    assert mapping.signatory_in_office("SIG-A", "2018-01-01", kg=kg) == []
    assert mapping.signatory_in_office("SIG-A", "2017-12-31", kg=kg)  # last day counts
    # by person name (any order / accents)
    assert mapping.signatory_in_office("Mballa Ekani Judith Pr", "2020-01-01", kg=kg)
    allrows = mapping.signatory_in_office("SIG-A", "2020-01-01", kg=kg, include_all=True)
    assert allrows[0]["in_office"] is False


def test_serial_usage_and_duplicates(kg):
    use = mapping.serial_usage("UFN-2016-00001", kg=kg)
    assert use["duplicate"] and use["count"] == 2 and use["holders"] == 2
    single = mapping.serial_usage("UFN-2016-00003", kg=kg)
    assert not single["duplicate"] and single["count"] == 1
    assert mapping.serial_usage("NOPE", kg=kg)["count"] == 0
    dups = mapping.duplicate_serials(kg=kg)
    assert [d["serial"] for d in dups] == ["UFN-2016-00001"]
    assert len(dups[0]["diplomas"]) == 2


def test_canned_queries_accept_plain_graph():
    rows = mapping.find_entries_by_name("TCHOUMI CAROLE", kg=build_graph())
    assert rows and rows[0]["mention"] == "Passable"


def test_validate_kg_matches_graph_validation(kg):
    _, rep_kg = mapping.validate_kg(kg)
    _, rep_g = mapping.validate(build_graph())
    assert mapping.violations_by_check(rep_kg) == mapping.violations_by_check(rep_g)


# ----------------------------------------------------------------------------- local client

def test_localkg_interface():
    k = LocalKG()
    assert k.health()
    assert kg_client.load_ontology(k) > 100
    assert k.triple_count(ONTOLOGY_GRAPH) == k.triple_count()
    n = k.load_turtle("@prefix ex:<http://e/> . ex:a ex:p 1, 2 ; ex:q 'x'@fr .", DATA_GRAPH)
    assert n == 3
    rows = k.sparql_select("SELECT ?o WHERE { <http://e/a> <http://e/p> ?o } ORDER BY ?o")
    assert [r["o"] for r in rows] == [1, 2]
    assert k.sparql_ask("ASK { <http://e/a> <http://e/q> ?x }")
    k.sparql_update("INSERT DATA { GRAPH <https://veridiplo.org/graph/data> { <http://e/b> <http://e/p> 9 } }")
    assert k.triple_count(DATA_GRAPH) == 4
    assert len(k.construct("CONSTRUCT { ?s <http://e/p> ?o } WHERE { ?s <http://e/p> ?o }")) == 3
    k.clear_graph(DATA_GRAPH)
    assert k.triple_count(DATA_GRAPH) == 0 and k.triple_count(ONTOLOGY_GRAPH) > 100


def test_make_kg_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv("KG_BACKEND", "auto")
    monkeypatch.setenv("KG_URL", "http://127.0.0.1:9")  # nothing listens here
    k = kg_client.make_kg()
    assert isinstance(k, LocalKG)
    monkeypatch.setenv("KG_BACKEND", "memory")
    assert isinstance(kg_client.make_kg(), LocalKG)
    kg_client.set_kg(None)


# ----------------------------------------------------------------------------- HTTP client (fake server)

class _FakeOxigraph(http.server.BaseHTTPRequestHandler):
    """Minimal /store /query /update server backed by LocalKG; mimics Oxigraph's HTTP surface."""

    store: LocalKG = LocalKG()
    seen_params: list = []

    def log_message(self, *a):  # silence
        pass

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()

    def _send(self, code, body="", ctype="text/plain"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        body = self._body()
        if url.path == "/store":
            graph = None if "default" in q else q["graph"][0]
            self.store.load_turtle(body, graph)
            self._send(204)
        elif url.path == "/update":
            self.store.sparql_update(body)
            self._send(204)
        elif url.path == "/query":
            type(self).seen_params.append(q.get("default-graph-uri"))
            accept = self.headers.get("Accept", "")
            res = self.store.ds.query(body)
            if res.type == "ASK":
                self._send(200, json.dumps({"head": {}, "boolean": bool(res.askAnswer)}),
                           "application/sparql-results+json")
            elif res.type == "CONSTRUCT":
                g = Graph()
                for t in res:
                    g.add(t)
                self._send(200, g.serialize(format="turtle"), "text/turtle")
            else:
                self._send(200, res.serialize(format="json").decode(), "application/sparql-results+json")
        else:
            self._send(404)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        if url.path == "/store":
            g = self.store.export_graph(None if "default" in q else q["graph"][0])
            self._send(200, g.serialize(format="turtle"), "text/turtle")
        else:
            self._send(404)


@pytest.fixture()
def http_kg():
    _FakeOxigraph.store = LocalKG()
    _FakeOxigraph.seen_params = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeOxigraph)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield OxigraphKG(f"http://127.0.0.1:{srv.server_address[1]}")
    srv.shutdown()


def test_http_client_against_fake_server(http_kg):
    assert http_kg.health()
    kg_client.load_ontology(http_kg)
    mapping.load_into_kg(build_graph(), http_kg)
    assert http_kg.triple_count(ONTOLOGY_GRAPH) > 100
    rows = mapping.find_entries_by_name("MBARGA FELIX", kg=http_kg)
    assert rows[0]["average"] == 17.2 and rows[0]["mention"] == "Très Bien"
    assert mapping.signatory_in_office("SIG-B", "2020-01-01", kg=http_kg)
    assert mapping.serial_usage("UFN-2016-00001", kg=http_kg)["duplicate"]
    assert mapping.programme_accreditation("UFN", "UFN-LIC-GC", session_year=2016, kg=http_kg)["covered"] is False
    # dataset (default-graph-uri) is sent so ontology + data graphs are queried together
    assert set(_FakeOxigraph.seen_params[-1]) == {ONTOLOGY_GRAPH, DATA_GRAPH}
    # validation of what the server holds flags the tampered diplomas
    _, rep = mapping.validate_kg(http_kg)
    assert flagged(rep)["C6"] == {"D-SIGNATORY"}
    http_kg.clear_graph(DATA_GRAPH)
    assert http_kg.triple_count(DATA_GRAPH) == 0
    assert not OxigraphKG("http://127.0.0.1:9", timeout=1).health()


@pytest.mark.skipif(not os.getenv("KG_TEST_URL"), reason="set KG_TEST_URL to run against a real Oxigraph")
def test_real_oxigraph():
    k = OxigraphKG(os.environ["KG_TEST_URL"])
    assert k.health()
    k.clear_graph(DATA_GRAPH)
    kg_client.load_ontology(k)
    mapping.load_into_kg(build_graph(), k)
    assert mapping.find_entries_by_name("KAMGA ERIC", kg=k)[0]["mention"] == "Assez Bien"
    _, rep = mapping.validate_kg(k)
    assert flagged(rep)["C3"] == {"D-ACCRED"}
    k.clear_graph(DATA_GRAPH)
