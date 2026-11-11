"""Plain dicts / Pydantic models <-> RDF (VeriDiplo ontology), SHACL validation and canned SPARQL.

Dicts are the exchange format between agents. Any object with ``model_dump()`` (Pydantic v2) or
``dict()`` is accepted wherever a dict is expected, so this module has no Pydantic dependency.

Entry contract (from the OCR agent)::

    {entry_id, document_id, page, bbox:[x0,y0,x1,y1], matricule, name_raw, name_norm,
     average (float|None), mention, decision, institution_id, programme_id, session_year, ocr_conf}
    optional: document_sha256, deliberation_date, birth_date, birth_place

Instance IRIs live under https://veridiplo.org/id/<kind>/<slug>.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Iterable

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, RDFS, SKOS, XSD

from .kg_client import ONTOLOGY_PATH, SHAPES_PATH, LocalKG, OxigraphKG, get_kg, load_ontology

VD = Namespace("https://veridiplo.org/ont#")
ID = Namespace("https://veridiplo.org/id/")
SCHEMA = Namespace("https://schema.org/")
SH = Namespace("http://www.w3.org/ns/shacl#")

# =============================================================================== helpers


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            return fn()
    raise TypeError(f"Cannot convert {type(obj).__name__} to dict")


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None and d[k] != "":
            return d[k]
    return default


def slug(value: Any) -> str:
    """IRI-safe slug (keeps letters, digits, dot, underscore, hyphen)."""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "x"


def iri(kind: str, key: Any) -> URIRef:
    return ID[f"{kind}/{slug(key)}"]


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalise_name(name: str) -> str:
    """Uppercase, accent-free, punctuation/hyphens -> spaces, whitespace collapsed."""
    s = strip_accents(str(name)).upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return " ".join(s.split())


def name_key(name: str) -> str:
    """Order-independent key: normalised tokens sorted alphabetically."""
    return " ".join(sorted(normalise_name(name).split()))


def _fmt_num(v: Any) -> str:
    d = Decimal(str(v)).normalize()
    return format(d, "f")


def bbox_to_str(bbox: Any) -> str | None:
    if bbox is None:
        return None
    if isinstance(bbox, str):
        return bbox
    vals = list(bbox)
    if len(vals) != 4:
        raise ValueError(f"bbox must have 4 numbers, got {bbox!r}")
    return ",".join(_fmt_num(v) for v in vals)


def bbox_from_str(text: str | None) -> list[float] | None:
    if not text:
        return None
    return [float(x) for x in text.split(",")]


def to_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return date.fromisoformat(s[:10])


def _date_lit(value: Any) -> Literal | None:
    d = to_date(value)
    return Literal(d.isoformat(), datatype=XSD.date) if d else None


def _dec(value: Any) -> Literal | None:
    if value is None or value == "":
        return None
    return Literal(Decimal(str(value)), datatype=XSD.decimal)


def _int(value: Any) -> Literal | None:
    if value is None or value == "":
        return None
    return Literal(int(value), datatype=XSD.integer)


def _add(g: Graph, s: URIRef, p: URIRef, o: Any) -> None:
    if o is not None:
        g.add((s, p, o if isinstance(o, (URIRef, Literal, BNode)) else Literal(o)))


def _new(g: Graph | None) -> Graph:
    if g is None:
        g = Graph()
    g.bind("vd", VD)
    g.bind("id", ID)
    g.bind("prov", PROV)
    g.bind("dcterms", DCTERMS)
    g.bind("schema", SCHEMA)
    g.bind("skos", SKOS)
    return g


# =============================================================================== mentions


@lru_cache(maxsize=1)
def ontology_graph() -> Graph:
    g = Graph()
    g.parse(ONTOLOGY_PATH, format="turtle")
    return g


@lru_cache(maxsize=1)
def shapes_graph() -> Graph:
    g = Graph()
    g.parse(SHAPES_PATH, format="turtle")
    return g


def _mention_key(label: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", strip_accents(str(label)).lower()).split())


@lru_cache(maxsize=1)
def _mention_index() -> dict[str, URIRef]:
    onto = ontology_graph()
    idx: dict[str, URIRef] = {}
    for m in onto.subjects(RDF.type, VD.Mention):
        for p in (SKOS.prefLabel, SKOS.altLabel, RDFS.label):
            for lab in onto.objects(m, p):
                idx[_mention_key(lab)] = m
        idx[_mention_key(str(m).rsplit("#", 1)[-1])] = m  # local name, e.g. "TresBien"
    extra = {"t bien": VD.TresBien, "tbien": VD.TresBien, "a bien": VD.AssezBien,
             "abien": VD.AssezBien, "ass bien": VD.AssezBien, "tres bien": VD.TresBien}
    for k, v in extra.items():
        idx.setdefault(k, v)
    return idx


def mention_iri(label: str | None) -> URIRef | None:
    """Map a French or English mention label (any case/accents) to a vd:Mention individual."""
    if not label:
        return None
    return _mention_index().get(_mention_key(label))


def mention_for_average(average: float | Decimal | None) -> URIRef | None:
    """Mention whose band contains the average (policy bands from the ontology)."""
    if average is None:
        return None
    a = Decimal(str(average))
    onto = ontology_graph()
    for m in onto.subjects(RDF.type, VD.Mention):
        lo = Decimal(str(onto.value(m, VD.minAverage)))
        hi = Decimal(str(onto.value(m, VD.maxAverage)))
        incl = bool(onto.value(m, VD.upperInclusive))
        if lo <= a and (a < hi or (incl and a == hi)):
            return m
    return None


# =============================================================================== builders


def _institution_stub(g: Graph, institution_id: Any) -> URIRef | None:
    if not institution_id:
        return None
    node = iri("institution", institution_id)
    g.add((node, RDF.type, VD.Institution))
    g.add((node, VD.institutionId, Literal(str(institution_id))))
    return node


def _programme_stub(g: Graph, programme_id: Any) -> URIRef | None:
    """Programme node WITHOUT vd:offeredBy: that is only asserted by institution_to_rdf (authoritative)."""
    if not programme_id:
        return None
    node = iri("programme", programme_id)
    g.add((node, RDF.type, VD.Programme))
    g.add((node, VD.programmeId, Literal(str(programme_id))))
    return node


def _session_node(g: Graph, programme_id: Any, institution_id: Any, year: Any) -> URIRef | None:
    if year in (None, ""):
        return None
    owner = programme_id or institution_id or "any"
    node = iri("session", f"{owner}-{int(year)}")
    g.add((node, RDF.type, VD.ExamSession))
    g.add((node, VD.sessionYear, _int(year)))
    if programme_id:
        g.add((node, VD.forProgramme, iri("programme", programme_id)))
    return node


def _document_node(g: Graph, document_id: Any, sha256: Any = None, title: Any = None,
                   pages: Any = None) -> URIRef | None:
    if not document_id:
        return None
    node = iri("document", document_id)
    g.add((node, RDF.type, VD.SourceDocument))
    g.add((node, DCTERMS.identifier, Literal(str(document_id))))
    _add(g, node, VD.sha256, Literal(str(sha256).lower()) if sha256 else None)
    _add(g, node, DCTERMS.title, Literal(title) if title else None)
    _add(g, node, VD.pageCount, _int(pages))
    return node


def _person_node(g: Graph, name_raw: str | None, name_norm: str | None,
                 birth_date: Any = None, birth_place: Any = None) -> URIRef | None:
    base = name_norm or name_raw
    if not base:
        return None
    key = name_key(base)
    bd = to_date(birth_date)
    node = iri("person", key.replace(" ", "-") + (f"-{bd.isoformat()}" if bd else ""))
    g.add((node, RDF.type, VD.Person))
    _add(g, node, SCHEMA.name, Literal(name_raw or name_norm))
    g.add((node, VD.nameNormalised, Literal(normalise_name(base))))
    g.add((node, VD.nameKey, Literal(key)))
    _add(g, node, SCHEMA.birthDate, _date_lit(bd))
    if birth_place:
        g.add((node, SCHEMA.birthPlace, Literal(str(birth_place))))
        g.add((node, VD.birthPlaceNorm, Literal(normalise_name(birth_place))))
    return node


def _set_mention(g: Graph, node: URIRef, label: Any) -> None:
    if not label:
        return
    g.add((node, VD.mentionRaw, Literal(str(label))))
    m = mention_iri(str(label))
    if m is not None:
        g.add((node, VD.mention, m))


def institution_to_rdf(inst: Any, g: Graph | None = None) -> Graph:
    """Institution with its programmes (+ accreditations) and signatories (terms of office).

    Keys: institution_id, name, name_en, language, private, serial_pattern,
    programmes:[{programme_id, name, name_en, level, accreditations:[{from,to}]}],
    signatories:[{signatory_id, name, office, in_office_from, in_office_to}]
    """
    d = _as_dict(inst)
    g = _new(g)
    iid = _pick(d, "institution_id", "id")
    node = _institution_stub(g, iid)
    if node is None:
        raise ValueError("institution needs institution_id")
    name = _pick(d, "name", "name_fr")
    if name:
        g.add((node, RDFS.label, Literal(name, lang=_pick(d, "language", default="fr")[:2])))
        g.add((node, SCHEMA.name, Literal(name)))
    if _pick(d, "name_en"):
        g.add((node, RDFS.label, Literal(d["name_en"], lang="en")))
    if d.get("private") is not None:
        g.add((node, SCHEMA.additionalType, Literal("private" if d["private"] else "public")))
    _add(g, node, VD.serialPattern, Literal(d["serial_pattern"]) if d.get("serial_pattern") else None)
    for prog in d.get("programmes") or []:
        programme_to_rdf({**_as_dict(prog), "institution_id": iid}, g)
    for sig in d.get("signatories") or []:
        signatory_to_rdf({**_as_dict(sig), "institution_id": iid}, g)
    return g


def programme_to_rdf(prog: Any, g: Graph | None = None) -> Graph:
    """Keys: programme_id, institution_id, name, name_en, level, accreditations:[{from,to,accreditation_id}]."""
    d = _as_dict(prog)
    g = _new(g)
    pid = _pick(d, "programme_id", "id")
    node = _programme_stub(g, pid)
    if node is None:
        raise ValueError("programme needs programme_id")
    iid = _pick(d, "institution_id")
    if iid:
        g.add((node, VD.offeredBy, _institution_stub(g, iid)))
    if _pick(d, "name"):
        g.add((node, RDFS.label, Literal(d["name"], lang="fr")))
        g.add((node, SCHEMA.name, Literal(d["name"])))
    if _pick(d, "name_en"):
        g.add((node, RDFS.label, Literal(d["name_en"], lang="en")))
    _add(g, node, VD.level, Literal(d["level"]) if d.get("level") else None)
    accs = list(d.get("accreditations") or [])
    for i, acc in enumerate(accs):
        a = _as_dict(acc)
        frm = _pick(a, "from", "accredited_from", "valid_from", "start")
        to = _pick(a, "to", "accredited_to", "valid_to", "end")
        anode = iri("accreditation", _pick(a, "accreditation_id", default=f"{pid}-{i + 1}"))
        g.add((anode, RDF.type, VD.Accreditation))
        g.add((anode, VD.forProgramme, node))
        _add(g, anode, VD.accreditedFrom, _date_lit(frm))
        _add(g, anode, VD.accreditedTo, _date_lit(to))
    return g


def signatory_to_rdf(sig: Any, g: Graph | None = None) -> Graph:
    """One term of office. Keys: signatory_id, name, office, institution_id, in_office_from, in_office_to."""
    d = _as_dict(sig)
    g = _new(g)
    sid = _pick(d, "signatory_id", "id")
    if not sid:
        raise ValueError("signatory needs signatory_id")
    node = iri("signatory", sid)
    g.add((node, RDF.type, VD.Signatory))
    g.add((node, VD.signatoryId, Literal(str(sid))))
    name = _pick(d, "name", "signatory_name")
    if name:
        g.add((node, SCHEMA.name, Literal(name)))
        g.add((node, VD.nameKey, Literal(name_key(name))))
    _add(g, node, VD.office, Literal(d["office"]) if d.get("office") else None)
    iid = _pick(d, "institution_id")
    if iid:
        g.add((node, VD.atInstitution, _institution_stub(g, iid)))
    _add(g, node, VD.inOfficeFrom, _date_lit(_pick(d, "in_office_from", "from")))
    _add(g, node, VD.inOfficeTo, _date_lit(_pick(d, "in_office_to", "to")))
    return g


def entry_to_rdf(entry: Any, g: Graph | None = None) -> Graph:
    """One PV row (OCR agent contract) -> vd:DeliberationEntry + Deliberation, Person, SourceDocument stubs."""
    d = _as_dict(entry)
    g = _new(g)
    eid = _pick(d, "entry_id")
    if not eid:
        raise ValueError("entry needs entry_id")
    node = iri("entry", eid)
    g.add((node, RDF.type, VD.DeliberationEntry))
    g.add((node, VD.entryId, Literal(str(eid))))

    iid, pid, year = d.get("institution_id"), d.get("programme_id"), d.get("session_year")
    inst = _institution_stub(g, iid)
    prog = _programme_stub(g, pid)
    sess = _session_node(g, pid, iid, year)
    doc_id = d.get("document_id")
    doc = _document_node(g, doc_id, sha256=d.get("document_sha256"))

    delib = None
    if doc_id:
        delib = iri("deliberation", doc_id)
        g.add((delib, RDF.type, VD.Deliberation))
        if inst:
            g.add((delib, VD.issuedBy, inst))
        if sess:
            g.add((delib, VD.inSession, sess))
        g.add((delib, PROV.wasDerivedFrom, doc))
        _add(g, delib, VD.deliberationDate, _date_lit(d.get("deliberation_date")))
        g.add((node, VD.recordedIn, delib))
        g.add((node, PROV.wasDerivedFrom, doc))
    if prog:
        g.add((node, VD.forProgramme, prog))
    if sess:
        g.add((node, VD.inSession, sess))

    person = _person_node(g, d.get("name_raw"), d.get("name_norm"), d.get("birth_date"), d.get("birth_place"))
    if person:
        g.add((node, VD.holder, person))
    _add(g, node, VD.nameRaw, Literal(d["name_raw"]) if d.get("name_raw") else None)
    _add(g, node, VD.nameNormalised, Literal(d["name_norm"]) if d.get("name_norm") else None)
    base = d.get("name_norm") or d.get("name_raw")
    if base:
        g.add((node, VD.nameKey, Literal(name_key(base))))
    _add(g, node, VD.matricule, Literal(str(d["matricule"])) if d.get("matricule") else None)
    _add(g, node, VD.average, _dec(d.get("average")))
    _set_mention(g, node, d.get("mention"))
    _add(g, node, VD.decision, Literal(d["decision"]) if d.get("decision") else None)
    _add(g, node, VD.ocrConfidence, _dec(_pick(d, "ocr_conf", "ocr_confidence")))
    _add(g, node, VD.sourcePage, _int(d.get("page")))
    bb = bbox_to_str(d.get("bbox"))
    _add(g, node, VD.bbox, Literal(bb) if bb else None)
    return g


def diploma_to_rdf(fields: Any, g: Graph | None = None) -> Graph:
    """A diploma (claim or registry record).

    Keys: diploma_id, serial (serial_number), holder_name (name_raw), name_norm, birth_date, birth_place,
    institution_id, programme_id, session_year, session_date, issue_date, signing_date,
    signatory_id, signatory_name, average, mention, matricule, sha256, document_id,
    verified_against (entry id or list of entry ids; asserted after check C1).
    """
    d = _as_dict(fields)
    g = _new(g)
    serial = _pick(d, "serial", "serial_number")
    did = _pick(d, "diploma_id", default=serial)
    if not did:
        raise ValueError("diploma needs diploma_id or serial")
    node = iri("diploma", did)
    g.add((node, RDF.type, VD.Diploma))
    _add(g, node, VD.serialNumber, Literal(str(serial)) if serial else None)

    name = _pick(d, "holder_name", "name_raw", "name")
    person = _person_node(g, name, d.get("name_norm"), d.get("birth_date"), d.get("birth_place"))
    if person:
        g.add((node, VD.holder, person))
    iid, pid, year = d.get("institution_id"), d.get("programme_id"), d.get("session_year")
    inst = _institution_stub(g, iid)
    if inst:
        g.add((node, VD.issuedBy, inst))
    prog = _programme_stub(g, pid)
    if prog:
        g.add((node, VD.forProgramme, prog))
    sess = _session_node(g, pid, iid, year)
    if sess:
        g.add((node, VD.inSession, sess))
        sdate = to_date(d.get("session_date"))
        if sdate:
            g.add((node, VD.sessionDate, _date_lit(sdate)))
        lo = sdate or date(int(year), 1, 1)
        hi = sdate or date(int(year), 12, 31)
        g.add((node, VD.periodStart, _date_lit(lo)))
        g.add((node, VD.periodEnd, _date_lit(hi)))
    _add(g, node, DCTERMS.issued, _date_lit(_pick(d, "issue_date", "issued")))
    _add(g, node, VD.signedOn, _date_lit(d.get("signing_date")))
    sid = d.get("signatory_id")
    if sid:
        sig = iri("signatory", sid)
        g.add((node, VD.signedBy, sig))
        if not (sig, RDF.type, VD.Signatory) in g:  # stub only; terms come from institution_to_rdf
            g.add((sig, RDF.type, VD.Signatory))
            g.add((sig, VD.signatoryId, Literal(str(sid))))
            if d.get("signatory_name"):
                g.add((sig, SCHEMA.name, Literal(d["signatory_name"])))
    _add(g, node, VD.average, _dec(d.get("average")))
    _set_mention(g, node, d.get("mention"))
    _add(g, node, VD.matricule, Literal(str(d["matricule"])) if d.get("matricule") else None)
    _add(g, node, VD.sha256, Literal(d["sha256"].lower()) if d.get("sha256") else None)
    doc = _document_node(g, d.get("document_id"), sha256=d.get("sha256"))
    if doc:
        g.add((node, PROV.wasDerivedFrom, doc))
    va = d.get("verified_against") or []
    for e in ([va] if isinstance(va, str) else va):
        g.add((node, VD.verifiedAgainst, iri("entry", e)))
    return g


def evidence_to_rdf(item: Any, g: Graph | None = None) -> Graph:
    """EvidenceItem. Keys: evidence_id, document_id, entry_id, page, bbox, sha256, supports[], contradicts[],
    check_id, case_id. Field names ("mention", "average", ...) become vd: property IRIs."""
    d = _as_dict(item)
    g = _new(g)
    eid = _pick(d, "evidence_id", "id")
    if not eid:
        raise ValueError("evidence needs evidence_id")
    node = iri("evidence", eid)
    g.add((node, RDF.type, VD.EvidenceItem))
    doc = _document_node(g, d.get("document_id"))
    if doc:
        g.add((node, PROV.wasDerivedFrom, doc))
    if d.get("entry_id"):
        g.add((node, PROV.wasDerivedFrom, iri("entry", d["entry_id"])))
    _add(g, node, VD.sourcePage, _int(_pick(d, "page", "source_page")))
    bb = bbox_to_str(d.get("bbox"))
    _add(g, node, VD.bbox, Literal(bb) if bb else None)
    _add(g, node, VD.sha256, Literal(d["sha256"].lower()) if d.get("sha256") else None)
    _add(g, node, VD.checkId, Literal(d["check_id"]) if d.get("check_id") else None)
    for key, prop in (("supports", VD.supports), ("contradicts", VD.contradicts)):
        for f in d.get(key) or []:
            g.add((node, prop, URIRef(f) if str(f).startswith("http") else VD[str(f)]))
    if d.get("case_id"):
        case = iri("case", d["case_id"])
        g.add((case, RDF.type, VD.VerificationCase))
        g.add((case, VD.hasEvidence, node))
        g.add((node, PROV.wasGeneratedBy, case))
    return g


def verdict_to_rdf(case: Any, g: Graph | None = None) -> Graph:
    """Keys: case_id, diploma_id, verdict, confidence, requires_review, consent_token_hash."""
    d = _as_dict(case)
    g = _new(g)
    cid = d["case_id"]
    cnode = iri("case", cid)
    g.add((cnode, RDF.type, VD.VerificationCase))
    if d.get("diploma_id"):
        g.add((cnode, VD.aboutDiploma, iri("diploma", d["diploma_id"])))
    _add(g, cnode, VD.consentTokenHash, Literal(d["consent_token_hash"]) if d.get("consent_token_hash") else None)
    vnode = iri("verdict", cid)
    g.add((vnode, RDF.type, VD.Verdict))
    g.add((vnode, VD.verdictValue, Literal(d["verdict"])))
    _add(g, vnode, VD.confidence, _dec(d.get("confidence")))
    if d.get("requires_review") is not None:
        g.add((vnode, VD.requiresReview, Literal(bool(d["requires_review"]))))
    g.add((vnode, PROV.wasGeneratedBy, cnode))
    g.add((cnode, VD.hasVerdict, vnode))
    return g


def policy_rule_to_rdf(rule: Any, g: Graph | None = None, provisions: dict[str, dict] | None = None) -> Graph:
    """Policy rule (kb/policy_rules YAML) -> vd:PolicyRule + vd:justifiedBy -> vd:LegalProvision.

    ``provisions`` optionally maps chunk_id -> KB chunk dict (article, binding, as_reported, title, url)."""
    d = _as_dict(rule)
    g = _new(g)
    rid = d["id"]
    node = iri("rule", rid)
    g.add((node, RDF.type, VD.PolicyRule))
    g.add((node, VD.ruleId, Literal(rid)))
    _add(g, node, VD.checkpoint, Literal(d["checkpoint"]) if d.get("checkpoint") else None)
    _add(g, node, VD.action, Literal(d["action"]) if d.get("action") else None)
    if d.get("proposal"):
        g.add((node, VD.proposal, Literal(True)))
    if d.get("message_fr"):
        g.add((node, RDFS.label, Literal(d["message_fr"], lang="fr")))
    if d.get("message_en"):
        g.add((node, RDFS.label, Literal(d["message_en"], lang="en")))
    for cid in d.get("justified_by") or []:
        prov_node = legal_provision_to_rdf({**(provisions or {}).get(cid, {}), "chunk_id": cid}, g)
        g.add((node, VD.justifiedBy, prov_node))
        g.add((prov_node, VD.justifies, node))
    return g


def legal_provision_to_rdf(chunk: Any, g: Graph | None = None) -> URIRef:
    d = _as_dict(chunk)
    g = _new(g) if g is not None else _new(None)
    cid = d["chunk_id"]
    node = iri("provision", cid)
    g.add((node, RDF.type, VD.LegalProvision))
    g.add((node, VD.chunkId, Literal(cid)))
    _add(g, node, VD.article, Literal(d["article"]) if d.get("article") else None)
    _add(g, node, DCTERMS.title, Literal(d["title"]) if d.get("title") else None)
    _add(g, node, DCTERMS.source, URIRef(d["url"]) if str(d.get("url", "")).startswith("http") else None)
    for key, prop in (("as_reported", VD.asReported), ("binding", VD.binding)):
        if d.get(key) is not None:
            g.add((node, prop, Literal(bool(d[key]))))
    return node


def gap_to_rdf(gap: Any, g: Graph | None = None) -> Graph:
    """Keys: gap_id, description, case_id, related_rules[]."""
    d = _as_dict(gap)
    g = _new(g)
    node = iri("gap", d["gap_id"])
    g.add((node, RDF.type, VD.PolicyGap))
    g.add((node, VD.gapId, Literal(d["gap_id"])))
    _add(g, node, RDFS.comment, Literal(d["description"]) if d.get("description") else None)
    if d.get("case_id"):
        g.add((node, VD.detectedIn, iri("case", d["case_id"])))
    for r in d.get("related_rules") or []:
        g.add((node, VD.relatedRule, iri("rule", r)))
    return g


# =============================================================================== reverse mapping


def _val(g: Graph, s: URIRef, p: URIRef) -> Any:
    o = g.value(s, p)
    if o is None:
        return None
    if isinstance(o, Literal):
        v = o.toPython()
        return float(v) if isinstance(v, Decimal) else v
    return str(o)


def _mention_label(g: Graph, node: URIRef) -> str | None:
    raw = _val(g, node, VD.mentionRaw)
    if raw:
        return raw
    m = g.value(node, VD.mention)
    if m is None:
        return None
    for lab in ontology_graph().objects(m, SKOS.prefLabel):
        if getattr(lab, "language", None) == "fr":
            return str(lab)
    return str(m).rsplit("#", 1)[-1]


def entry_from_rdf(g: Graph, node: URIRef | str) -> dict[str, Any]:
    """Inverse of entry_to_rdf (returns the OCR-agent entry contract)."""
    node = URIRef(node) if not isinstance(node, URIRef) else node
    delib = g.value(node, VD.recordedIn)
    doc = g.value(node, PROV.wasDerivedFrom)
    prog = g.value(node, VD.forProgramme)
    sess = g.value(node, VD.inSession)
    inst = g.value(delib, VD.issuedBy) if delib is not None else None
    return {
        "entry_id": _val(g, node, VD.entryId),
        "document_id": str(g.value(doc, DCTERMS.identifier)) if doc is not None and g.value(doc, DCTERMS.identifier) else None,
        "page": _val(g, node, VD.sourcePage),
        "bbox": bbox_from_str(_val(g, node, VD.bbox)),
        "matricule": _val(g, node, VD.matricule),
        "name_raw": _val(g, node, VD.nameRaw),
        "name_norm": _val(g, node, VD.nameNormalised),
        "average": _val(g, node, VD.average),
        "mention": _mention_label(g, node),
        "decision": _val(g, node, VD.decision),
        "institution_id": _val(g, inst, VD.institutionId) if inst is not None else None,
        "programme_id": _val(g, prog, VD.programmeId) if prog is not None else None,
        "session_year": _val(g, sess, VD.sessionYear) if sess is not None else None,
        "ocr_conf": _val(g, node, VD.ocrConfidence),
    }


def entries_from_graph(g: Graph) -> list[dict[str, Any]]:
    return [entry_from_rdf(g, n) for n in sorted(g.subjects(RDF.type, VD.DeliberationEntry))]


def diploma_from_rdf(g: Graph, node: URIRef | str) -> dict[str, Any]:
    """Inverse of diploma_to_rdf."""
    node = URIRef(node) if not isinstance(node, URIRef) else node
    holder, inst = g.value(node, VD.holder), g.value(node, VD.issuedBy)
    prog, sess, sig = g.value(node, VD.forProgramme), g.value(node, VD.inSession), g.value(node, VD.signedBy)

    def iso(p: URIRef) -> str | None:
        v = _val(g, node, p)
        return v.isoformat() if v else None

    return {
        "diploma_id": str(node).rsplit("/", 1)[-1],
        "serial": _val(g, node, VD.serialNumber),
        "holder_name": _val(g, holder, SCHEMA.name) if holder is not None else None,
        "name_norm": _val(g, holder, VD.nameNormalised) if holder is not None else None,
        "birth_date": (_val(g, holder, SCHEMA.birthDate).isoformat()
                       if holder is not None and _val(g, holder, SCHEMA.birthDate) else None),
        "birth_place": _val(g, holder, SCHEMA.birthPlace) if holder is not None else None,
        "institution_id": _val(g, inst, VD.institutionId) if inst is not None else None,
        "programme_id": _val(g, prog, VD.programmeId) if prog is not None else None,
        "session_year": _val(g, sess, VD.sessionYear) if sess is not None else None,
        "session_date": iso(VD.sessionDate),
        "issue_date": iso(DCTERMS.issued),
        "signing_date": iso(VD.signedOn),
        "signatory_id": _val(g, sig, VD.signatoryId) if sig is not None else None,
        "average": _val(g, node, VD.average),
        "mention": _mention_label(g, node),
        "matricule": _val(g, node, VD.matricule),
        "sha256": _val(g, node, VD.sha256),
        "verified_against": sorted(str(_val(g, e, VD.entryId) or str(e).rsplit("/", 1)[-1])
                                   for e in g.objects(node, VD.verifiedAgainst)),
    }


def diplomas_from_graph(g: Graph) -> list[dict[str, Any]]:
    return [diploma_from_rdf(g, n) for n in sorted(g.subjects(RDF.type, VD.Diploma))]


# =============================================================================== SHACL validation

_RESULT_MSG_LANGS = ("en", "fr")


def validate(graph: Graph, include_ontology: bool = True, shapes: Graph | None = None
             ) -> tuple[bool, list[dict[str, Any]]]:
    """Validate ``graph`` against the VeriDiplo shapes with pyshacl.

    The ontology is merged into the data graph first (mention bands are data). Returns
    ``(conforms, report)``; each report item is a dict::

        {check_id, severity, focus_node, path, value, message, message_en, message_fr,
         source_shape, constraint_component}

    ``check_id`` is the ``vd:checkId`` of the violated shape (C2..C7, or STRUCT).
    """
    import pyshacl

    shapes = shapes if shapes is not None else shapes_graph()
    data = Graph()
    for t in graph:
        data.add(t)
    if include_ontology:
        for t in ontology_graph():
            data.add(t)
    conforms, report_graph, _ = pyshacl.validate(
        data, shacl_graph=shapes, inference="none", abort_on_first=False, meta_shacl=False, advanced=False)

    report: list[dict[str, Any]] = []
    for res in report_graph.subjects(RDF.type, SH.ValidationResult):
        msgs = {}
        for m in report_graph.objects(res, SH.resultMessage):  # pyshacl drops language tags: recover them
            msgs[_message_langs(shapes).get(str(m)) or "en"] = str(m)
        shape = report_graph.value(res, SH.sourceShape)
        check = shapes.value(shape, VD.checkId) if shape is not None else None
        value = report_graph.value(res, SH.value)
        path = report_graph.value(res, SH.resultPath)
        sev = report_graph.value(res, SH.resultSeverity)
        report.append({
            "check_id": str(check) if check is not None else "STRUCT",
            "severity": str(sev).rsplit("#", 1)[-1] if sev is not None else "Violation",
            "focus_node": str(report_graph.value(res, SH.focusNode)),
            "path": str(path) if path is not None else None,
            "value": _to_plain(value),
            "message": msgs.get("en") or next(iter(msgs.values()), ""),
            "message_en": msgs.get("en", ""),
            "message_fr": msgs.get("fr", ""),
            "source_shape": str(shape) if shape is not None else None,
            "constraint_component": str(report_graph.value(res, SH.sourceConstraintComponent)).rsplit("#", 1)[-1],
        })
    report.sort(key=lambda r: (r["focus_node"], r["check_id"], r["message"]))
    return bool(conforms), report


def _message_langs(shapes: Graph) -> dict[str, str]:
    cache = _MSG_LANG_CACHE.setdefault(id(shapes), {})
    if not cache:
        for m in shapes.objects(None, SH.message):
            if isinstance(m, Literal) and m.language:
                cache[str(m)] = m.language
    return cache


_MSG_LANG_CACHE: dict[int, dict[str, str]] = {}


def _to_plain(term: Any) -> Any:
    if isinstance(term, Literal):
        return term.toPython() if not isinstance(term.toPython(), Literal) else str(term)
    return None if term is None else str(term)


def validate_kg(kg: LocalKG | OxigraphKG | None = None) -> tuple[bool, list[dict[str, Any]]]:
    """Export everything the KG holds and validate it."""
    kg = kg or get_kg()
    return validate(kg.export_graph(), include_ontology=True)


def violations_by_check(report: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """{check_id: {focus node IRIs}} - handy for asserting which diplomas fail which check."""
    out: dict[str, set[str]] = {}
    for r in report:
        out.setdefault(r["check_id"], set()).add(r["focus_node"])
    return out


# =============================================================================== canned SPARQL

PREFIXES = """\
PREFIX vd: <https://veridiplo.org/ont#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX schema: <https://schema.org/>
"""


def _q(value: Any) -> str:
    """Quote a python string as a SPARQL string literal."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _resolve_kg(kg: Any) -> LocalKG | OxigraphKG:
    """Accept a KG client, an rdflib Graph (wrapped in a throw-away LocalKG with the ontology) or None."""
    if kg is None:
        return get_kg()
    if isinstance(kg, Graph):
        tmp = LocalKG()
        tmp.load_graph(ontology_graph(), "https://veridiplo.org/graph/ontology")
        tmp.load_graph(kg)
        return tmp
    return kg


def q_find_entries_by_name(name_norm: str, institution: str | None = None, year: int | None = None,
                           mode: str = "exact") -> str:
    key = name_key(name_norm)
    if mode == "exact":
        name_filter = f"FILTER(?key = {_q(key)})"
    else:  # "token": any token of >= 3 letters shared with the entry key
        toks = [t for t in key.split() if len(t) >= 3] or key.split()
        pattern = "|".join(re.escape(t) for t in toks)
        name_filter = f'FILTER(REGEX(?key, {_q(f"(^| )({pattern})( |$)")}))'
    inst_filter = f"FILTER(?institution_id = {_q(institution)})" if institution else ""
    year_filter = f"FILTER(?year = {int(year)})" if year is not None else ""
    return PREFIXES + f"""
SELECT ?entry ?entry_id ?name_raw ?name_norm ?key ?average ?mention ?mention_raw ?decision ?matricule
       ?page ?bbox ?ocr_conf ?document ?institution_id ?programme_id ?year
WHERE {{
    ?entry a vd:DeliberationEntry ; vd:nameKey ?key ; vd:entryId ?entry_id .
    {name_filter}
    OPTIONAL {{ ?entry vd:nameRaw ?name_raw }}
    OPTIONAL {{ ?entry vd:nameNormalised ?name_norm }}
    OPTIONAL {{ ?entry vd:average ?average }}
    OPTIONAL {{ ?entry vd:mention ?m . ?m skos:prefLabel ?mention . FILTER(LANG(?mention) = "fr") }}
    OPTIONAL {{ ?entry vd:mentionRaw ?mention_raw }}
    OPTIONAL {{ ?entry vd:decision ?decision }}
    OPTIONAL {{ ?entry vd:matricule ?matricule }}
    OPTIONAL {{ ?entry vd:sourcePage ?page }}
    OPTIONAL {{ ?entry vd:bbox ?bbox }}
    OPTIONAL {{ ?entry vd:ocrConfidence ?ocr_conf }}
    OPTIONAL {{ ?entry prov:wasDerivedFrom ?document }}
    OPTIONAL {{ ?entry vd:recordedIn/vd:issuedBy/vd:institutionId ?institution_id }}
    OPTIONAL {{ ?entry vd:forProgramme/vd:programmeId ?programme_id }}
    OPTIONAL {{ ?entry vd:inSession/vd:sessionYear ?year }}
    {inst_filter}
    {year_filter}
}}
ORDER BY ?year ?entry_id"""


def find_entries_by_name(name_norm: str, institution: str | None = None, year: int | None = None,
                         kg: Any = None, mode: str = "exact") -> list[dict[str, Any]]:
    """PV entries whose (token-sorted, normalised) name equals ``name_norm``'s; optional institution id / year.

    ``mode="token"`` relaxes to entries sharing any name token (candidates for fuzzy re-ranking)."""
    return _resolve_kg(kg).sparql_select(q_find_entries_by_name(name_norm, institution, year, mode))


def q_programme_accreditation(institution: str, programme: str) -> str:
    return PREFIXES + f"""
SELECT ?accreditation ?programme_id ?institution_id ?offered ?from ?to
WHERE {{
    ?programme vd:programmeId {_q(programme)} .
    BIND({_q(institution)} AS ?institution_id)
    OPTIONAL {{ ?programme vd:offeredBy ?inst . ?inst vd:institutionId ?offeredId . }}
    BIND(BOUND(?offeredId) && ?offeredId = ?institution_id AS ?offered)
    OPTIONAL {{
        ?accreditation vd:forProgramme ?programme ; vd:accreditedFrom ?from .
        OPTIONAL {{ ?accreditation vd:accreditedTo ?to }}
    }}
    BIND({_q(programme)} AS ?programme_id)
}}
ORDER BY ?from"""


def programme_accreditation(institution: str, programme: str, session_year: int | None = None,
                            at_date: Any = None, kg: Any = None) -> dict[str, Any]:
    """Accreditation periods of a programme at an institution (check C3).

    Returns ``{offered, accreditations:[{accreditation, from, to, covers}], covered}``.
    ``covered`` is True when some period overlaps ``at_date`` (or the whole of ``session_year``);
    None when neither is given. ``offered`` is False if the programme is not offered by ``institution``."""
    rows = _resolve_kg(kg).sparql_select(q_programme_accreditation(institution, programme))
    offered = any(r.get("offered") for r in rows)
    if at_date is not None:
        lo = hi = to_date(at_date)
    elif session_year is not None:
        lo, hi = date(int(session_year), 1, 1), date(int(session_year), 12, 31)
    else:
        lo = hi = None
    accs = []
    for r in rows:
        if "accreditation" not in r:
            continue
        frm, to = r.get("from"), r.get("to")
        covers = None if lo is None else bool(frm <= hi and (to is None or to >= lo))
        accs.append({"accreditation": r["accreditation"], "from": frm, "to": to, "covers": covers})
    covered = None if lo is None else any(a["covers"] for a in accs)
    return {"offered": offered, "accreditations": accs, "covered": covered}


def q_signatory_terms(signatory: str, institution: str | None = None) -> str:
    inst = f"FILTER(?institution_id = {_q(institution)})" if institution else ""
    return PREFIXES + f"""
SELECT ?signatory ?signatory_id ?name ?office ?institution_id ?from ?to
WHERE {{
    ?signatory a vd:Signatory ; vd:inOfficeFrom ?from .
    OPTIONAL {{ ?signatory vd:signatoryId ?signatory_id }}
    OPTIONAL {{ ?signatory schema:name ?name }}
    OPTIONAL {{ ?signatory vd:nameKey ?key }}
    OPTIONAL {{ ?signatory vd:office ?office }}
    OPTIONAL {{ ?signatory vd:inOfficeTo ?to }}
    OPTIONAL {{ ?signatory vd:atInstitution/vd:institutionId ?institution_id }}
    FILTER( ?signatory_id = {_q(signatory)} || ?key = {_q(name_key(signatory))} )
    {inst}
}}
ORDER BY ?from"""


def signatory_in_office(signatory: str, at: Any, institution: str | None = None, kg: Any = None,
                        include_all: bool = False) -> list[dict[str, Any]]:
    """Terms of office of ``signatory`` (signatory id or person name) and whether each covers date ``at`` (C6).

    Returns only terms in office at ``at`` unless ``include_all``; each row carries ``in_office``."""
    d = to_date(at)
    rows = _resolve_kg(kg).sparql_select(q_signatory_terms(signatory, institution))
    for r in rows:
        r["in_office"] = bool(r["from"] <= d and (r.get("to") is None or d <= r["to"]))
    return rows if include_all else [r for r in rows if r["in_office"]]


def q_serial_usage(serial: str) -> str:
    return PREFIXES + f"""
SELECT ?diploma ?serial ?holder_name ?key ?institution_id ?year ?issued ?average ?mention_raw
WHERE {{
    ?diploma a vd:Diploma ; vd:serialNumber ?serial .
    FILTER(?serial = {_q(serial)})
    OPTIONAL {{ ?diploma vd:holder ?h . ?h vd:nameKey ?key . OPTIONAL {{ ?h schema:name ?holder_name }} }}
    OPTIONAL {{ ?diploma vd:issuedBy/vd:institutionId ?institution_id }}
    OPTIONAL {{ ?diploma vd:inSession/vd:sessionYear ?year }}
    OPTIONAL {{ ?diploma dcterms:issued ?issued }}
    OPTIONAL {{ ?diploma vd:average ?average }}
    OPTIONAL {{ ?diploma vd:mentionRaw ?mention_raw }}
}}
ORDER BY ?diploma"""


def serial_usage(serial: str, kg: Any = None) -> dict[str, Any]:
    """Every diploma carrying ``serial`` (C7 uniqueness). ``duplicate`` is True when more than one
    distinct holder uses it."""
    rows = _resolve_kg(kg).sparql_select(q_serial_usage(serial))
    holders = {r.get("key") for r in rows if r.get("key")}
    return {"serial": serial, "count": len(rows), "holders": len(holders), "duplicate": len(holders) > 1,
            "diplomas": rows}


Q_DUPLICATE_SERIALS = PREFIXES + """
SELECT ?serial (COUNT(DISTINCT ?key) AS ?holders) (GROUP_CONCAT(DISTINCT STR(?diploma); separator=" ") AS ?diplomas)
WHERE {
    ?diploma a vd:Diploma ; vd:serialNumber ?serial ; vd:holder/vd:nameKey ?key .
}
GROUP BY ?serial
HAVING (COUNT(DISTINCT ?key) > 1)
ORDER BY ?serial"""


def duplicate_serials(kg: Any = None) -> list[dict[str, Any]]:
    """All serial numbers held by more than one distinct holder."""
    rows = _resolve_kg(kg).sparql_select(Q_DUPLICATE_SERIALS)
    for r in rows:
        r["diplomas"] = r["diplomas"].split()
    return rows


def load_into_kg(graph: Graph, kg: LocalKG | OxigraphKG | None = None, ensure_ontology: bool = True) -> int:
    """Insert instance data into the KG data graph (loading the ontology first when missing)."""
    kg = kg or get_kg()
    if ensure_ontology and kg.triple_count("https://veridiplo.org/graph/ontology") == 0:
        load_ontology(kg)
    return kg.load_graph(graph)
