"""EvidenceRetrievalAgent (Y.3172 node M): finds the documentary evidence for a diploma claim.

Sources queried
  (a) PV rows of the archive, matched on the normalised holder name (rapidfuzz token-sort: accent and
      name-order insensitive), scoped to institution + programme + session;
  (b) registry rows (name match, plus ALL rows carrying the same serial number for the uniqueness check C7);
  (c) the knowledge graph via canned SPARQL (accreditation periods, signatory terms of office, serial format).

Every returned ``EvidenceItem`` carries provenance (document id/sha256/file, page, row, bbox, page size,
chunk id, crop URL, OCR confidence).  Retrieval metadata that the ConsistencyAgent needs lives in
``item.fields["_match"]`` (role, name_similarity, scope, scoped_candidates, session_year, deliberation_date...).

``naive=True`` is Version A (ungoverned): no institution / session scoping, the globally most name-similar
row is returned even when the similarity is low (the "guessing" behaviour of scenario S5).
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Text as SAText
from sqlalchemy import cast, or_, select
from sqlalchemy.orm import Session, defer

from app.agents.matching import SERIAL_KEYS, name_sim, row_name, row_serial, token_align_sim  # noqa: F401
from app.core.models import Chunk, SourceDocument
from app.core.names import normalize_name
from app.core.schemas import DiplomaFields, EvidenceItem

log = logging.getLogger(__name__)

MATCH_MIN = 80.0  # below this a name is not considered a match (C1 band lower bound)
WIDEN_MIN = 92.0  # outside the claimed session only a confident name match is kept (no look-alike guessing)
MAX_SERIAL_HOLDERS = 6


# ----------------------------------------------------------------------------- row helpers


class _Row:
    __slots__ = ("chunk", "doc", "sim", "name")

    def __init__(self, chunk: Chunk, doc: SourceDocument, name: str, sim: float):
        self.chunk, self.doc, self.name, self.sim = chunk, doc, name, sim


def _base_query(kind: str, institution: Optional[str], programme: Optional[str], year: Optional[int]):
    q = (select(Chunk, SourceDocument).join(SourceDocument, SourceDocument.id == Chunk.document_id)
         .options(defer(Chunk.embedding)).where(Chunk.kind == kind))
    if institution:
        q = q.where(Chunk.institution_id == institution)
    if programme:
        q = q.where(Chunk.programme_id == programme)
    if year is not None:
        q = q.where(Chunk.session_year == year)
    return q


def _score_rows(session: Session, kind: str, target: str, institution=None, programme=None, year=None) -> list[_Row]:
    rows = []
    for chunk, doc in session.execute(_base_query(kind, institution, programme, year)).all():
        nm = row_name(chunk.fields or {}, chunk.name_norm)
        rows.append(_Row(chunk, doc, nm, name_sim(target, nm)))
    rows.sort(key=lambda r: -r.sim)
    return rows


# ----------------------------------------------------------------------------- KG facts
_local_kg = None


def _institutions_json() -> Optional[Path]:
    cands = [Path(os.getenv("DATA_DIR", "/app/data")) / "synthetic" / "institutions.json",
             Path(__file__).resolve().parents[2] / "data" / "synthetic" / "institutions.json"]
    return next((p for p in cands if p.exists()), None)


def _fallback_kg():
    """In-memory KG built from the synthetic institutions file (used when the KG service is empty/unreachable)."""
    global _local_kg
    if _local_kg is not None:
        return _local_kg
    import json

    from app.kg import mapping
    from app.kg.kg_client import LocalKG, load_ontology

    path = _institutions_json()
    if path is None:
        return None
    kg = LocalKG()
    load_ontology(kg)
    for inst in json.loads(path.read_text(encoding="utf-8")):
        d = {
            "institution_id": inst["id"], "name": inst.get("name_fr"), "name_en": inst.get("name_en"),
            "language": inst.get("language", "fr"), "private": inst.get("kind") == "private",
            # the regex is stored in vd:serialPattern (docs/ontology.md: "holds the serial-number regex")
            "serial_pattern": inst.get("serial_format"),
            "programmes": [{"programme_id": p["id"], "name": p.get("name_fr"), "name_en": p.get("name_en"),
                            "level": p.get("level"), "accreditations": p.get("accreditations", [])}
                           for p in inst.get("programmes", [])],
            "signatories": [{"signatory_id": s["id"], "name": s["name"], "office": s.get("title"),
                             "in_office_from": s.get("in_office_from"), "in_office_to": s.get("in_office_to")}
                            for s in inst.get("signatories", [])],
        }
        mapping.load_into_kg(mapping.institution_to_rdf(d), kg, ensure_ontology=False)
    _local_kg = kg
    return kg


def _kg_has_institutions(kg) -> bool:
    from app.kg import mapping
    try:
        return bool(kg.sparql_ask(mapping.PREFIXES + "ASK { ?s a vd:Signatory ; vd:inOfficeFrom ?f }"))
    except Exception as exc:
        log.warning("KG not queryable (%s)", exc)
        return False


def get_fact_kg():
    """KG client holding institution facts: the live KG when seeded, otherwise the synthetic fallback."""
    from app.kg.kg_client import get_kg
    try:
        kg = get_kg()
        if _kg_has_institutions(kg):
            return kg
    except Exception as exc:
        log.warning("KG client unavailable (%s)", exc)
    return _fallback_kg()


def _iso(d: Any) -> Optional[str]:
    return d.isoformat() if hasattr(d, "isoformat") else (str(d) if d else None)


def _regex_from_pattern(p: Optional[str]) -> Optional[str]:
    """Serial regex from vd:serialPattern: a regex already, or a token pattern like UFN/{YYYY}/{PROG}/{NNNNN}."""
    if not p:
        return None
    import re
    if not re.search(r"\{(?:YYYY|PROG|N+)\}", p):  # already a regex (may contain quantifiers such as \d{4})
        return p
    out = re.escape(p)
    out = out.replace(r"\{YYYY\}", r"\d{4}").replace(r"\{PROG\}", r"[A-Z]{3,7}")
    out = re.sub(r"\\\{(N+)\\\}", lambda m: r"\d{%d}" % len(m.group(1)), out)
    return "^" + out + "$"


def kg_facts(fields: DiplomaFields) -> dict[str, Any]:
    """Canned SPARQL: accreditation periods, signatory terms, serial format. Values are JSON-serialisable."""
    from app.kg import mapping
    kg = get_fact_kg()
    facts: dict[str, Any] = {"available": kg is not None}
    if kg is None:
        return facts
    inst, prog = fields.institution_id, fields.programme_id
    if inst and prog:
        try:
            acc = mapping.programme_accreditation(inst, prog, session_year=fields.session_year, kg=kg)
            known = bool(kg.sparql_ask(mapping.PREFIXES + f"""
ASK {{ ?i a vd:Institution ; vd:institutionId {mapping._q(inst)} . ?p vd:offeredBy ?i }}"""))
            facts["accreditation"] = {
                "institution_id": inst, "programme_id": prog, "offered": bool(acc["offered"]),
                "institution_known": known,
                "periods": [{"from": _iso(a["from"]), "to": _iso(a["to"]), "accreditation": str(a["accreditation"])}
                            for a in acc["accreditations"]],
            }
        except Exception as exc:
            log.warning("accreditation query failed: %s", exc)
    if fields.signatory_name:
        try:
            rows = mapping.signatory_in_office(fields.signatory_name, date(2000, 1, 1), kg=kg, include_all=True)
            facts["signatory"] = {
                "signatory_name": fields.signatory_name, "institution_id": inst,
                "terms": [{"signatory_id": r.get("signatory_id"), "name": r.get("name"),
                           "institution_id": r.get("institution_id"), "office": r.get("office"),
                           "from": _iso(r.get("from")), "to": _iso(r.get("to"))} for r in rows],
            }
        except Exception as exc:
            log.warning("signatory query failed: %s", exc)
    if inst:
        try:
            rows = kg.sparql_select(mapping.PREFIXES + f"""
SELECT ?pattern WHERE {{ ?i a vd:Institution ; vd:institutionId {mapping._q(inst)} ; vd:serialPattern ?pattern }}""")
            if rows:
                facts["serial"] = {"institution_id": inst, "pattern": rows[0]["pattern"],
                                   "regex": _regex_from_pattern(rows[0]["pattern"])}
        except Exception as exc:
            log.warning("serial pattern query failed: %s", exc)
    return facts


# ----------------------------------------------------------------------------- agent
class EvidenceRetrievalAgent:
    name = "EvidenceRetrievalAgent"
    node = "M"

    def retrieve(self, session: Session, fields: DiplomaFields, naive: bool = False) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        target = normalize_name(fields.holder_name or "")
        if target:
            items += self._pv_items(session, fields, target, naive)
            items += self._registry_items(session, fields, target, naive)
        items += self._serial_holder_items(session, fields, items)
        items += self._kg_items(fields)
        for i, it in enumerate(items, start=1):
            it.evidence_id = f"E{i}"
        return items

    # ---- (a) PV rows
    def _pv_items(self, session: Session, f: DiplomaFields, target: str, naive: bool) -> list[EvidenceItem]:
        if naive:
            rows = _score_rows(session, "pv_row", target)
            if not rows:
                return []
            return [self._row_item(rows[0], "best_pv", scope="global", scoped=0,
                                   note="Version A: nearest name over the whole archive, no scoping")]
        scoped = _score_rows(session, "pv_row", target, f.institution_id, f.programme_id, f.session_year)
        best_scoped = scoped[0] if scoped else None
        if best_scoped is not None and best_scoped.sim < MATCH_MIN:
            # no name close enough by token-sort: keep the candidate that is closest token by token
            best_scoped = max(scoped, key=lambda r: token_align_sim(target, r.name))
        if best_scoped and best_scoped.sim >= MATCH_MIN:
            return [self._row_item(best_scoped, "best_pv", scope="session", scoped=len(scoped))]
        # not found in the claimed session: widen (same programme, then same institution) before concluding
        for scope, prog in (("programme", f.programme_id), ("institution", None)):
            if scope == "programme" and not f.programme_id:
                continue
            if scope == "institution" and not f.institution_id:
                continue
            wide = _score_rows(session, "pv_row", target, f.institution_id, prog, None)
            if wide and wide[0].sim >= WIDEN_MIN:
                note = (f"Name found outside the claimed session/programme "
                        f"(PV session {wide[0].chunk.session_year}, programme {wide[0].chunk.programme_id})")
                return [self._row_item(wide[0], "best_pv", scope=scope, scoped=len(scoped), note=note)]
        if best_scoped is not None:  # candidates exist in the right institution/session but none resembles the name
            return [self._row_item(best_scoped, "best_pv", scope="session", scoped=len(scoped))]
        return []  # nothing resembling the holder anywhere in scope: no PV evidence

    # ---- (b) registry rows
    def _registry_items(self, session: Session, f: DiplomaFields, target: str, naive: bool) -> list[EvidenceItem]:
        if naive:
            rows = _score_rows(session, "registry_row", target)
            return [self._row_item(rows[0], "registry_match", scope="global", scoped=0)] if rows else []
        scoped = _score_rows(session, "registry_row", target, f.institution_id, None, f.session_year)
        if scoped and scoped[0].sim >= MATCH_MIN:
            return [self._row_item(scoped[0], "registry_match", scope="session", scoped=len(scoped))]
        if f.institution_id:
            wide = _score_rows(session, "registry_row", target, f.institution_id, None, None)
            if wide and wide[0].sim >= WIDEN_MIN:
                return [self._row_item(wide[0], "registry_match", scope="institution", scoped=len(scoped),
                                       note="Registry row found outside the claimed session")]
        return []

    def _serial_holder_items(self, session: Session, f: DiplomaFields, existing: list[EvidenceItem]) -> list[EvidenceItem]:
        """ALL registry rows carrying the diploma's serial number (uniqueness check C7)."""
        serial = "".join((f.serial or "").split()).upper()
        if not serial:
            return []
        q = (select(Chunk, SourceDocument).join(SourceDocument, SourceDocument.id == Chunk.document_id)
             .options(defer(Chunk.embedding)).where(Chunk.kind == "registry_row")
             .where(or_(Chunk.text.ilike(f"%{serial}%"), cast(Chunk.fields, SAText).ilike(f"%{serial}%"))))
        have = {it.chunk_id: it for it in existing}
        target = normalize_name(f.holder_name or "")
        out: list[EvidenceItem] = []
        for chunk, doc in session.execute(q).all():
            fl = chunk.fields or {}
            rs = row_serial(fl)
            if rs is not None and rs != serial:
                continue
            if rs is None and serial not in (chunk.text or "").upper().replace(" ", ""):
                continue
            nm = row_name(fl, chunk.name_norm)
            row = _Row(chunk, doc, nm, name_sim(target, nm))
            if chunk.id in have:  # already returned as the registry name match: just tag it
                have[chunk.id].fields.setdefault("_match", {})["serial_holder"] = True
                continue
            if len(out) < MAX_SERIAL_HOLDERS:
                out.append(self._row_item(row, "serial_holder", scope="serial", scoped=0, serial_holder=True))
        return out

    # ---- (c) knowledge graph
    def _kg_items(self, f: DiplomaFields) -> list[EvidenceItem]:
        try:
            facts = kg_facts(f)
        except Exception as exc:  # KG failure must never break retrieval
            log.warning("KG facts unavailable: %s", exc)
            return []
        out: list[EvidenceItem] = []

        def add(kind: str, fields: dict[str, Any], snippet: str, score: float = 1.0):
            out.append(EvidenceItem(evidence_id="", kind=kind, source_file="knowledge-graph (SPARQL)",
                                    snippet=snippet, fields={**fields, "_match": {"role": kind, "scope": "kg"}},
                                    score=score))
        acc = facts.get("accreditation")
        if acc:
            per = "; ".join(f"{p['from']} -> {p['to'] or 'en cours'}" for p in acc["periods"]) or "aucune période"
            add("accreditation", acc, f"Accréditation {acc['programme_id']}@{acc['institution_id']}: "
                                      f"{'offert' if acc['offered'] else 'non offert'}; {per}")
        sig = facts.get("signatory")
        if sig:
            per = "; ".join(f"{t['institution_id']}: {t['from']} -> {t['to'] or 'en cours'}" for t in sig["terms"]) \
                or "aucun mandat enregistré"
            add("signatory", sig, f"Signataire {sig['signatory_name']}: {per}")
        ser = facts.get("serial")
        if ser:
            add("kg_fact", {**ser, "fact": "serial_format"}, f"Format de numéro {ser['institution_id']}: {ser['pattern']}")
        return out

    # ---- item builder
    def _row_item(self, r: _Row, role: str, scope: str, scoped: int, note: Optional[str] = None,
                  serial_holder: bool = False) -> EvidenceItem:
        c, d = r.chunk, r.doc
        meta = d.meta or {}
        fields = dict(c.fields or {})
        fields["_match"] = {
            "role": role, "name_similarity": round(r.sim, 2), "scope": scope, "scoped_candidates": scoped,
            "session_year": c.session_year, "programme_id": c.programme_id, "institution_id": c.institution_id,
            "deliberation_date": meta.get("deliberation_date") or fields.get("deliberation_date"),
            "row_name": r.name, "note": note, "serial_holder": serial_holder,
        }
        return EvidenceItem(
            evidence_id="", kind=c.kind, document_id=c.document_id, source_file=d.filename, sha256=d.sha256,
            page=c.page, row_idx=c.row_idx, bbox=list(c.bbox) if c.bbox is not None else None,
            page_size=list(c.page_size) if c.page_size is not None else None, chunk_id=c.id,
            snippet=" ".join((c.text or "").split())[:240], fields=fields, score=round(r.sim / 100.0, 4),
            crop_url=f"/search/crop/{c.id}.png", ocr_conf=c.ocr_conf)
