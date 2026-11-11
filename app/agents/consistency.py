"""ConsistencyAgent (Y.3172 node M): field-by-field checks C1-C8 (CLAUDE.md 4.2) -> verdict.

The checks are pure functions of ``(DiplomaFields, list[EvidenceItem], mode)``; no database access.

Verdict logic (decisions documented in docs/eval_verification.md)
  * any ``fail`` on a critical check (C1, C3, C4, C5, C6, C7) backed by evidence -> INCONSISTENT
    (C5 is critical by project decision: the "wrong session date" tampering is only visible through it);
  * no PV and no registry evidence at all                              -> INSUFFICIENT_EVIDENCE;
  * a critical check is ``unknown``                                    -> INSUFFICIENT_EVIDENCE + human review;
  * all critical checks ``pass``                                       -> CONSISTENT;
  * confidence = weighted pass ratio x mean OCR confidence of the evidence used.

``mode="off"`` is the strict, naive Version A: no ``unknown`` band for names (similarity >= 92 passes, anything
else fails), so an OCR-damaged name of a genuine holder yields a false INCONSISTENT (scenario S6).

Wording: the words "faux", "fake" and "fraud" never appear in any output; the vocabulary is fixed.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from app.agents.matching import name_sim, row_name, row_serial, token_align_sim
from app.core.names import normalize_name
from app.core.normalize import mention_for_average, normalize_mention, parse_average, parse_date
from app.core.schemas import CheckResult, DiplomaFields, EvidenceItem, VerdictOut

CRITICAL = ("C1", "C3", "C4", "C5", "C6", "C7")
WEIGHTS = {"C1": 3.0, "C2": 1.0, "C3": 2.0, "C4": 3.0, "C5": 2.0, "C6": 2.0, "C7": 2.0, "C8": 0.5}
FIELD_OF = {"C1": "holder_name", "C2": "birth_date", "C3": "programme_id", "C4": "average/mention",
            "C5": "session_year", "C6": "signatory_name", "C7": "serial", "C8": "image"}
DIPLOMA_FIELDS_OF = {"C1": ["holder_name"], "C2": ["birth_date", "birth_place"], "C3": ["programme_id"],
                     "C4": ["average", "mention"], "C5": ["session_year", "issue_date"], "C6": ["signatory_name"],
                     "C7": ["serial"]}

NAME_PASS = 92.0
NAME_UNKNOWN = 80.0
NAIVE_NAME_PASS = 92.0  # Version A: no unknown band, same bar as the governed pass threshold
PLACE_PASS = 85.0
AVG_TOL = 0.011  # tolerance 0.01 (float noise safe)

FORBIDDEN = ("faux", "fake", "fraud")


# ----------------------------------------------------------------------------- small helpers
def _m(it: EvidenceItem) -> dict[str, Any]:
    return (it.fields or {}).get("_match") or {}


def _ids(*items: Optional[EvidenceItem]) -> list[str]:
    return [i.evidence_id for i in items if i is not None]


def _d(v: Any) -> Optional[date]:
    iso = parse_date(v)
    return date.fromisoformat(iso) if iso else None


def _res(cid: str, status: str, fr: str, en: str, ev: list[str], score: Optional[float] = None) -> CheckResult:
    return CheckResult(check_id=cid, field=FIELD_OF[cid], status=status, evidence_ids=ev, detail=fr,
                       detail_en=en, critical=cid in CRITICAL, score=score)


def _sim(fields: DiplomaFields, it: EvidenceItem) -> float:
    return name_sim(fields.holder_name or "", row_name(it.fields or {}) or _m(it).get("row_name") or "")


class _Ctx:
    """Evidence grouped for the checks."""

    def __init__(self, fields: DiplomaFields, evidence: list[EvidenceItem]):
        self.pv = sorted((e for e in evidence if e.kind == "pv_row"), key=lambda e: -_sim(fields, e))
        self.reg = [e for e in evidence if e.kind == "registry_row"]
        self.acc = next((e for e in evidence if e.kind == "accreditation"), None)
        self.sig = next((e for e in evidence if e.kind == "signatory"), None)
        self.serial_fact = next((e for e in evidence if e.kind == "kg_fact"
                                 and (e.fields or {}).get("fact") == "serial_format"), None)
        self.anchor: Optional[EvidenceItem] = None  # row the holder was matched to (set by C1 when it passes)
        self.person_rows: list[EvidenceItem] = []  # rows confirmed to be about the holder


# ----------------------------------------------------------------------------- checks
def check_c1(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    if not normalize_name(fields.holder_name or ""):
        return _res("C1", "unknown", "Nom du titulaire absent du diplôme saisi.", "Holder name missing from the diploma.", [])
    cands = ctx.pv or sorted([e for e in ctx.reg], key=lambda e: -_sim(fields, e))
    if cands and _sim(fields, cands[0]) < NAME_UNKNOWN:  # nothing close by token-sort: prefer the token-wise closest row
        cands = sorted(cands, key=lambda e: -token_align_sim(fields.holder_name or "", row_name(e.fields or {})))
    if not cands:
        return _res("C1", "unknown",
                    "Aucune pièce (PV ou registre) trouvée pour ce titulaire : preuve insuffisante.",
                    "No PV or registry record found for this holder: insufficient evidence.", [])
    best = cands[0]
    sim = _sim(fields, best)
    where = f"{best.source_file}, p.{best.page}" + (f", ligne {best.row_idx}" if best.row_idx is not None else "")
    where_en = where.replace("ligne", "row")
    row_nm = row_name(best.fields or {})
    s = round(sim / 100.0, 3)
    ok_fr = f"Nom concordant avec la pièce « {row_nm} » (similarité {sim:.1f}) [{where}]."
    ok_en = f"Name matches record “{row_nm}” (similarity {sim:.1f}) [{where_en}]."
    if sim >= NAME_PASS:
        ctx.anchor = best
        return _res("C1", "pass", ok_fr, ok_en, _ids(best), s)
    if mode == "off":  # strict naive comparison: no uncertainty band
        return _res("C1", "fail", f"Nom différent de la pièce « {row_nm} » (similarité {sim:.1f}) [{where}].",
                    f"Name differs from record “{row_nm}” (similarity {sim:.1f}) [{where_en}].", _ids(best), s)
    if sim >= NAME_UNKNOWN:
        return _res("C1", "unknown",
                    f"Nom proche mais non identique à « {row_nm} » (similarité {sim:.1f}, zone 80-92) : revue humaine "
                    f"requise (variante d'orthographe, ordre des noms ou erreur de lecture possibles) [{where}].",
                    f"Name close to but not identical with “{row_nm}” (similarity {sim:.1f}, 80-92 band): human review "
                    f"required (spelling variant, name order or reading error possible) [{where_en}].", _ids(best), s)
    aligned = token_align_sim(fields.holder_name or "", row_nm)
    if aligned >= NAME_UNKNOWN:  # token-wise close (e.g. one OCR slip that changes the token order): never a fail
        return _res("C1", "unknown",
                    f"Nom proche de « {row_nm} » mot à mot (similarité {sim:.1f}, alignement {aligned:.1f}) : revue humaine "
                    f"requise, erreur de lecture possible [{where}].",
                    f"Name close to “{row_nm}” token by token (similarity {sim:.1f}, alignment {aligned:.1f}): human review "
                    f"required, reading error possible [{where_en}].", _ids(best), s)
    scoped = _m(best).get("scoped_candidates")
    if _m(best).get("scope", "session") == "session" and (scoped is None or scoped > 0):
        return _res("C1", "fail",
                    f"Aucune ligne du PV de l'établissement/session ne correspond au nom (meilleure similarité {sim:.1f} "
                    f"avec « {row_nm} ») [{where}].",
                    f"No row of the institution/session PV matches the name (best similarity {sim:.1f} with "
                    f"“{row_nm}”) [{where_en}].", _ids(best), s)
    return _res("C1", "unknown", "Aucune pièce comparable dans l'établissement et la session indiqués.",
                "No comparable record for the stated institution and session.", [])


def _birth_rows(ctx: _Ctx) -> list[EvidenceItem]:
    return ctx.person_rows


def check_c2(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    if not fields.birth_date and not fields.birth_place:
        return _res("C2", "unknown", "Date et lieu de naissance non fournis.", "Birth date and place not provided.", [])
    if ctx.anchor is None:
        return _res("C2", "unknown", "Titulaire non rapproché d'une pièce : naissance non comparable.",
                    "Holder not matched to a record: birth data not comparable.", [])
    claim_d = _d(fields.birth_date)
    claim_p = normalize_name(fields.birth_place or "")
    used: list[EvidenceItem] = []
    problems_fr, problems_en = [], []
    compared = False
    for row in ctx.person_rows:
        fl = row.fields or {}
        rd = _d(fl.get("birth_date") or fl.get("date_naissance"))
        rp = normalize_name(fl.get("birth_place") or fl.get("lieu_naissance") or "")
        if claim_d and rd:
            compared = True
            used.append(row)
            if claim_d != rd:
                problems_fr.append(f"date de naissance {claim_d.isoformat()} ≠ {rd.isoformat()}")
                problems_en.append(f"birth date {claim_d.isoformat()} ≠ {rd.isoformat()}")
        if claim_p and rp:
            compared = True
            if row not in used:
                used.append(row)
            if name_sim(claim_p, rp) < PLACE_PASS:
                problems_fr.append(f"lieu de naissance « {claim_p} » ≠ « {rp} »")
                problems_en.append(f"birth place “{claim_p}” ≠ “{rp}”")
    ev = _ids(*used)
    if problems_fr:
        return _res("C2", "fail", "Incohérence de naissance : " + " ; ".join(problems_fr) + ".",
                    "Birth data mismatch: " + "; ".join(problems_en) + ".", ev, 0.0)
    if not compared:
        return _res("C2", "unknown", "Les pièces retrouvées ne contiennent pas de données de naissance exploitables.",
                    "The retrieved records hold no usable birth data.", [])
    return _res("C2", "pass", "Date/lieu de naissance concordants avec le registre.",
                "Birth date/place consistent with the registry.", ev, 1.0)


def _periods(acc: EvidenceItem) -> list[tuple[Optional[date], Optional[date]]]:
    return [(_d(p.get("from")), _d(p.get("to"))) for p in (acc.fields or {}).get("periods", [])]


def check_c3(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    acc = ctx.acc
    if acc is None:
        return _res("C3", "unknown", "Données d'accréditation indisponibles dans la base de connaissances.",
                    "Accreditation data unavailable in the knowledge graph.", [])
    prog, inst = fields.programme_id, fields.institution_id
    if not (acc.fields or {}).get("offered"):
        if (acc.fields or {}).get("institution_known") is False:  # the reference data does not know the institution at all
            return _res("C3", "unknown", f"Établissement {inst} absent du référentiel : accréditation non vérifiable.",
                        f"Institution {inst} is not in the reference data: accreditation cannot be verified.", _ids(acc))
        return _res("C3", "fail", f"Le programme {prog} n'est pas rattaché à l'établissement {inst} dans le référentiel.",
                    f"Programme {prog} is not offered by institution {inst} in the reference data.", _ids(acc), 0.0)
    day = None
    basis_fr = basis_en = ""
    pv = ctx.anchor if ctx.anchor is not None and ctx.anchor.kind == "pv_row" else None
    if pv is not None:
        day = _d(_m(pv).get("deliberation_date"))
        if day:
            basis_fr, basis_en = f"à la date de délibération {day.isoformat()}", f"at the deliberation date {day.isoformat()}"
    if day is not None:
        lo = hi = day
    elif fields.session_year:
        lo, hi = date(int(fields.session_year), 1, 1), date(int(fields.session_year), 12, 31)
        basis_fr, basis_en = f"sur l'année de session {fields.session_year}", f"over session year {fields.session_year}"
    else:
        return _res("C3", "unknown", "Session non indiquée : accréditation non évaluable.",
                    "Session not stated: accreditation cannot be assessed.", _ids(acc))
    periods = _periods(acc)
    covered = any(f is not None and f <= hi and (t is None or t >= lo) for f, t in periods)
    txt = "; ".join(f"{f} → {t or 'en cours'}" for f, t in periods) or "aucune"
    if covered:
        return _res("C3", "pass", f"Programme accrédité {basis_fr} (périodes : {txt}).",
                    f"Programme accredited {basis_en} (periods: {txt}).", _ids(acc, pv), 1.0)
    return _res("C3", "fail", f"Programme non accrédité {basis_fr} (périodes : {txt}).",
                f"Programme not accredited {basis_en} (periods: {txt}).", _ids(acc, pv), 0.0)


def check_c4(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    claim_avg = fields.average
    claim_m = normalize_mention(fields.mention)
    if claim_avg is None and not claim_m:
        return _res("C4", "unknown", "Moyenne et mention absentes du diplôme saisi.",
                    "Average and mention missing from the diploma.", [])
    if ctx.anchor is None:
        return _res("C4", "unknown", "Titulaire non rapproché d'une ligne de PV/registre : moyenne et mention non comparables.",
                    "Holder not matched to a PV/registry row: average and mention not comparable.", [])
    rows = [ctx.anchor] + [r for r in ctx.person_rows if r is not ctx.anchor and r.kind != ctx.anchor.kind]
    # source of truth: the PV row; the registry is only used when the PV row lacks the value
    row_avg = row_m = None
    used: list[EvidenceItem] = []
    for r in rows:
        fl = r.fields or {}
        a, mm = parse_average(fl.get("average") if fl.get("average") is not None else fl.get("moyenne")), \
            normalize_mention(fl.get("mention"))
        if row_avg is None and a is not None:
            row_avg = a
            used.append(r)
        if row_m is None and mm:
            row_m = mm
            if r not in used:
                used.append(r)
    issues_fr, issues_en = [], []
    compared = False
    if claim_avg is not None and row_avg is not None:
        compared = True
        if abs(float(claim_avg) - row_avg) > AVG_TOL:
            issues_fr.append(f"moyenne {claim_avg:.2f} sur le diplôme ≠ {row_avg:.2f} au PV")
            issues_en.append(f"average {claim_avg:.2f} on the diploma ≠ {row_avg:.2f} in the record")
    if claim_m and row_m:
        compared = True
        if claim_m != row_m:
            issues_fr.append(f"mention « {claim_m} » sur le diplôme ≠ « {row_m} » au PV")
            issues_en.append(f"mention “{claim_m}” on the diploma ≠ “{row_m}” in the record")
    if claim_avg is not None and claim_m:
        compared = True
        band = mention_for_average(float(claim_avg))
        if band and band != claim_m:
            issues_fr.append(f"mention « {claim_m} » incohérente avec la moyenne {claim_avg:.2f} (barème : « {band} »)")
            issues_en.append(f"mention “{claim_m}” inconsistent with average {claim_avg:.2f} (band: “{band}”)")
    ev = _ids(*(used or [ctx.anchor]))
    if issues_fr:
        return _res("C4", "fail", "Écart sur les résultats : " + " ; ".join(issues_fr) + ".",
                    "Results mismatch: " + "; ".join(issues_en) + ".", ev, 0.0)
    if not compared:
        return _res("C4", "unknown", "La ligne retrouvée ne contient ni moyenne ni mention lisible.",
                    "The matched row holds no readable average or mention.", ev)
    return _res("C4", "pass", f"Moyenne ({claim_avg}) et mention ({claim_m or '-'}) concordantes avec la source et le barème.",
                f"Average ({claim_avg}) and mention ({claim_m or '-'}) consistent with the source and the grading bands.", ev, 1.0)


def check_c5(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    a = ctx.anchor
    if a is None:
        return _res("C5", "unknown", "Titulaire non rapproché d'un PV : cohérence des dates non évaluable.",
                    "Holder not matched to a PV: date coherence cannot be assessed.", [])
    mt = _m(a)
    pv_year = mt.get("session_year") or (a.fields or {}).get("session_year")
    pv_prog = mt.get("programme_id")
    delib = _d(mt.get("deliberation_date"))
    issue = _d(fields.issue_date)
    issues_fr, issues_en = [], []
    compared = 0
    if fields.session_year and pv_year:
        compared += 1
        if int(fields.session_year) != int(pv_year):
            issues_fr.append(f"session {fields.session_year} sur le diplôme, mais le titulaire figure au PV de la session {pv_year}")
            issues_en.append(f"session {fields.session_year} on the diploma, but the holder appears in the {pv_year} session PV")
    if fields.programme_id and pv_prog:
        compared += 1
        if fields.programme_id != pv_prog:
            issues_fr.append(f"programme {fields.programme_id} sur le diplôme, mais PV du programme {pv_prog}")
            issues_en.append(f"programme {fields.programme_id} on the diploma, but PV of programme {pv_prog}")
    if issue and delib:
        compared += 1
        if issue <= delib:
            issues_fr.append(f"date de délivrance {issue.isoformat()} non postérieure à la délibération du {delib.isoformat()}")
            issues_en.append(f"issue date {issue.isoformat()} not after the deliberation of {delib.isoformat()}")
    sess_for_issue = pv_year or fields.session_year
    if issue and sess_for_issue:
        compared += 1
        if issue.year < int(sess_for_issue):
            issues_fr.append(f"date de délivrance {issue.isoformat()} antérieure à l'année de session {sess_for_issue}")
            issues_en.append(f"issue date {issue.isoformat()} earlier than session year {sess_for_issue}")
    ev = _ids(a)
    if issues_fr:
        return _res("C5", "fail", "Incohérence de dates : " + " ; ".join(issues_fr) + ".",
                    "Date inconsistency: " + "; ".join(issues_en) + ".", ev, 0.0)
    if not compared:
        return _res("C5", "unknown", "Dates insuffisantes pour évaluer la cohérence session/délibération/délivrance.",
                    "Not enough dates to assess session/deliberation/issue coherence.", ev)
    extra_fr = "" if delib else " (date de délibération non disponible)"
    extra_en = "" if delib else " (deliberation date not available)"
    return _res("C5", "pass", "Session et dates cohérentes avec le PV" + extra_fr + ".",
                "Session and dates coherent with the PV" + extra_en + ".", ev, 1.0 if delib else 0.8)


def check_c6(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    if not fields.signatory_name:
        return _res("C6", "unknown", "Signataire non indiqué.", "Signatory not stated.", [])
    issue = _d(fields.issue_date)
    if issue is None:
        return _res("C6", "unknown", "Date de signature/délivrance absente : mandat non évaluable.",
                    "Signing/issue date missing: term of office cannot be assessed.", [])
    sig = ctx.sig
    if sig is None:
        return _res("C6", "unknown", "Mandats des signataires indisponibles dans la base de connaissances.",
                    "Signatory terms of office unavailable in the knowledge graph.", [])
    want = normalize_name(fields.signatory_name)
    terms = [t for t in (sig.fields or {}).get("terms", [])
             if not t.get("name") or name_sim(want, t["name"]) >= NAME_PASS]
    if fields.institution_id:
        here = [t for t in terms if not t.get("institution_id") or t["institution_id"] == fields.institution_id]
    else:
        here = terms
    if not here:
        other = ", ".join(sorted({str(t.get("institution_id")) for t in terms})) if terms else ""
        return _res("C6", "fail",
                    f"Aucun mandat enregistré pour « {fields.signatory_name} » à {fields.institution_id}"
                    + (f" (mandat connu ailleurs : {other})" if other else "") + ".",
                    f"No term of office recorded for “{fields.signatory_name}” at {fields.institution_id}"
                    + (f" (term known elsewhere: {other})" if other else "") + ".", _ids(sig), 0.0)
    txt = "; ".join(f"{t.get('from')} → {t.get('to') or 'en cours'}" for t in here)
    for t in here:
        f, to = _d(t.get("from")), _d(t.get("to"))
        if f is not None and f <= issue and (to is None or issue <= to):
            return _res("C6", "pass", f"Signataire en fonction à la date du {issue.isoformat()} (mandat : {txt}).",
                        f"Signatory in office on {issue.isoformat()} (term: {txt}).", _ids(sig), 1.0)
    return _res("C6", "fail", f"Signataire hors mandat à la date du {issue.isoformat()} (mandats : {txt}).",
                f"Signatory out of office on {issue.isoformat()} (terms: {txt}).", _ids(sig), 0.0)


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def check_c7(fields: DiplomaFields, ctx: _Ctx, mode: str) -> CheckResult:
    serial = "".join((fields.serial or "").split()).upper()
    if not serial:
        return _res("C7", "unknown", "Numéro de série absent du diplôme saisi.", "Serial number missing from the diploma.", [])
    fmt = ctx.serial_fact
    regex = (fmt.fields or {}).get("regex") if fmt else None
    if regex:
        try:
            ok = re.fullmatch(regex, serial) is not None
        except re.error:
            ok = True
        if not ok:
            return _res("C7", "fail", f"Format du numéro {serial} invalide pour l'établissement (attendu : {fmt.fields.get('pattern')}).",
                        f"Serial {serial} has an invalid format for the institution (expected: {fmt.fields.get('pattern')}).",
                        _ids(fmt), 0.0)
        m = _YEAR.search(serial)
        if m and fields.session_year and int(m.group(0)) != int(fields.session_year):
            return _res("C7", "fail", f"L'année du numéro {serial} ({m.group(0)}) diffère de la session {fields.session_year}.",
                        f"The year in serial {serial} ({m.group(0)}) differs from session {fields.session_year}.", _ids(fmt), 0.0)
    holders = [r for r in ctx.reg if row_serial(r.fields or {}) == serial]
    others = [r for r in holders if _sim(fields, r) < NAME_UNKNOWN]
    if others:
        names = ", ".join(sorted({f"« {row_name(r.fields or {})} »" for r in others}))
        return _res("C7", "fail", f"Le numéro {serial} est aussi attribué à un autre titulaire dans le registre : {names}.",
                    f"Serial {serial} is also held by a different holder in the registry: {names}.",
                    _ids(*others), 0.0)
    if holders:
        return _res("C7", "pass", f"Numéro {serial} au format valide, unique et rattaché à ce titulaire dans le registre.",
                    f"Serial {serial} has a valid format, is unique and belongs to this holder in the registry.",
                    _ids(*holders, fmt), 1.0)
    if regex:
        return _res("C7", "pass", f"Numéro {serial} au format valide ; aucun autre titulaire trouvé (couverture du registre non confirmée).",
                    f"Serial {serial} has a valid format; no other holder found (registry coverage not confirmed).",
                    _ids(fmt), 0.6)
    return _res("C7", "unknown", "Format de numéro et registre indisponibles : unicité non évaluable.",
                "Serial format and registry unavailable: uniqueness cannot be assessed.", [])


def check_c8(diploma_ocr_conf: Optional[float]) -> CheckResult:
    """Low-weight image-integrity hint, never decisive: only an OCR-confidence anomaly can lower it to unknown."""
    if diploma_ocr_conf is None:
        return _res("C8", "unknown", "Contrôle d'intégrité de l'image non effectué (indice facultatif).",
                    "Image integrity hint not run (optional hint).", [])
    if diploma_ocr_conf < 0.5:
        return _res("C8", "unknown", f"Confiance OCR du diplôme faible ({diploma_ocr_conf:.2f}) : lecture à confirmer.",
                    f"Low OCR confidence on the diploma ({diploma_ocr_conf:.2f}): reading to be confirmed.", [], diploma_ocr_conf)
    return _res("C8", "pass", f"Aucune anomalie de confiance OCR (moyenne {diploma_ocr_conf:.2f}).",
                f"No OCR-confidence anomaly (mean {diploma_ocr_conf:.2f}).", [], diploma_ocr_conf)


# ----------------------------------------------------------------------------- pipeline
def run_checks(fields: DiplomaFields, evidence: list[EvidenceItem], mode: str = "on",
               diploma_ocr_conf: Optional[float] = None) -> tuple[list[CheckResult], VerdictOut]:
    """Pure function: C1-C8 and the verdict. Annotates ``evidence`` items with supports/contradicts."""
    ctx = _Ctx(fields, evidence)
    c1 = check_c1(fields, ctx, mode)
    if c1.status == "pass" and ctx.anchor is not None:
        # rows confirmed to be about the holder: the anchor plus registry rows matching the name
        ctx.person_rows = [ctx.anchor] + [r for r in ctx.reg if r is not ctx.anchor and _sim(fields, r) >= NAME_PASS]
    checks = [c1, check_c2(fields, ctx, mode), check_c3(fields, ctx, mode), check_c4(fields, ctx, mode),
              check_c5(fields, ctx, mode), check_c6(fields, ctx, mode), check_c7(fields, ctx, mode),
              check_c8(diploma_ocr_conf)]
    by_id = {e.evidence_id: e for e in evidence}
    for c in checks:
        for eid in c.evidence_ids:
            e = by_id.get(eid)
            if e is None:
                continue
            tgt = e.supports if c.status == "pass" else e.contradicts if c.status == "fail" else None
            if tgt is not None:
                for f in DIPLOMA_FIELDS_OF.get(c.check_id, []):
                    if f not in tgt:
                        tgt.append(f)
    return checks, build_verdict(checks, evidence)


def build_verdict(checks: list[CheckResult], evidence: list[EvidenceItem]) -> VerdictOut:
    crit = [c for c in checks if c.check_id in CRITICAL]
    fails = [c for c in crit if c.status == "fail" and c.evidence_ids]
    unknown_crit = [c for c in crit if c.status == "unknown" or (c.status == "fail" and not c.evidence_ids)]
    docs = [e for e in evidence if e.kind in ("pv_row", "registry_row")]
    c2_fail = any(c.check_id == "C2" and c.status == "fail" for c in checks)

    total_w = sum(WEIGHTS.values())
    ratio = sum(WEIGHTS[c.check_id] * (c.score if c.score is not None and c.status == "pass" else 1.0)
                for c in checks if c.status == "pass") / total_w
    confs = [e.ocr_conf for e in docs if e.ocr_conf is not None]
    ocr = sum(confs) / len(confs) if confs else 1.0
    conf = round(max(0.0, min(1.0, ratio * ocr)), 3)

    if fails:
        label, human = "INCONSISTENT", True
        fr = [f"{c.check_id} : {c.detail}" for c in fails]
        en = [f"{c.check_id}: {c.detail_en}" for c in fails]
        fr.append("Écart relevé entre le diplôme et les sources : validation par le registraire requise avant toute communication.")
        en.append("Discrepancy found between the diploma and the sources: registrar validation required before any communication.")
    elif not docs:
        label, human = "INSUFFICIENT_EVIDENCE", True
        fr = ["Aucune pièce d'archive (PV ou registre) retrouvée : l'authenticité ne peut être ni confirmée ni infirmée.",
              "Une demande de recherche d'archives auprès de l'établissement est recommandée."]
        en = ["No archive record (PV or registry) found: consistency can be neither confirmed nor refuted.",
              "A request to the institution to search its archives is recommended."]
    elif unknown_crit:
        label, human = "INSUFFICIENT_EVIDENCE", True
        fr = [f"{c.check_id} : {c.detail}" for c in unknown_crit] + ["Revue humaine requise."]
        en = [f"{c.check_id}: {c.detail_en}" for c in unknown_crit] + ["Human review required."]
    else:
        label, human = "CONSISTENT", c2_fail
        fr = ["Toutes les vérifications critiques concordent avec les sources citées."]
        en = ["All critical checks agree with the cited sources."]
        if c2_fail:
            fr.append("Écart sur la date/le lieu de naissance (non bloquant) : revue humaine conseillée.")
            en.append("Birth date/place discrepancy (non-blocking): human review advised.")
    return VerdictOut(label=label, confidence=conf, reasons_fr=fr, reasons_en=en, needs_human=human)


class ConsistencyAgent:
    name = "ConsistencyAgent"
    node = "M"

    def check(self, session, fields: DiplomaFields, evidence: list[EvidenceItem], mode: str = "on",
              diploma_ocr_conf: Optional[float] = None) -> tuple[list[CheckResult], VerdictOut]:
        """C1-C8 -> (checks, verdict). ``session`` is unused (kept for the agent interface)."""
        return run_checks(fields, evidence, mode=mode, diploma_ocr_conf=diploma_ocr_conf)


def contains_forbidden(*texts: str) -> bool:
    blob = " ".join(texts).lower()
    return any(w in blob for w in FORBIDDEN)


# ----------------------------------------------------------------------------- optional SHACL cross-check
def shacl_crosscheck(fields: DiplomaFields, evidence: list[EvidenceItem], checks: list[CheckResult]) -> dict[str, Any]:
    """Re-derive the structural checks with the SHACL shapes of ``app/kg`` and compare with the pure-python results.

    Opt-in (costs about a second per case: pyshacl). The diploma is linked to its PV entry (``verified_against``)
    only when C1 passed, as docs/ontology.md requires. Returns ``{"shacl_violations": [check ids],
    "python_failures": [check ids], "agree": bool}`` restricted to C2-C7 (C1 and C8 are not SHACL checks).
    """
    from app.kg import mapping

    ctx = _Ctx(fields, evidence)
    g = None
    inst, prog = fields.institution_id, fields.programme_id
    d: dict[str, Any] = {"institution_id": inst}
    serial_fact = ctx.serial_fact
    if serial_fact is not None:
        d["serial_pattern"] = serial_fact.fields.get("regex")
    if ctx.acc is not None and prog:
        d["programmes"] = [{"programme_id": prog, "accreditations": [
            {"from": p.get("from"), "to": p.get("to")} for p in ctx.acc.fields.get("periods", [])]}]
    terms = (ctx.sig.fields.get("terms", []) if ctx.sig is not None else [])
    sig_id = None
    issue = _d(fields.issue_date)
    want = normalize_name(fields.signatory_name or "")
    sigs = []
    for i, t in enumerate(terms):
        if want and t.get("name") and name_sim(want, t["name"]) < NAME_PASS:
            continue
        sid = t.get("signatory_id") or f"T{i}"
        sigs.append({"signatory_id": sid, "name": t.get("name") or fields.signatory_name, "institution_id": t.get("institution_id") or inst,
                     "in_office_from": t.get("from"), "in_office_to": t.get("to")})
        f_, to_ = _d(t.get("from")), _d(t.get("to"))
        if sig_id is None and issue and f_ and f_ <= issue and (to_ is None or issue <= to_):
            sig_id = sid
    if sigs and sig_id is None:
        sig_id = next((s["signatory_id"] for s in sigs if s["institution_id"] == inst), sigs[0]["signatory_id"])
    d["signatories"] = sigs
    g = mapping.institution_to_rdf(d) if inst else mapping.Graph()

    c1 = next(c for c in checks if c.check_id == "C1")
    entry_id = None
    anchor = ctx.pv[0] if ctx.pv else None
    if c1.status == "pass" and anchor is not None and anchor.chunk_id is not None:
        entry_id = f"CHUNK-{anchor.chunk_id}"
        mt = _m(anchor)
        fl = anchor.fields or {}
        mapping.entry_to_rdf({
            "entry_id": entry_id, "document_id": anchor.document_id, "page": anchor.page, "bbox": anchor.bbox,
            "name_raw": fl.get("name_raw"), "name_norm": fl.get("name_norm"), "average": parse_average(fl.get("average")),
            "mention": fl.get("mention"), "decision": fl.get("decision"), "institution_id": inst,
            "programme_id": mt.get("programme_id") or prog, "session_year": mt.get("session_year") or fields.session_year,
            "deliberation_date": mt.get("deliberation_date"), "document_sha256": anchor.sha256,
            "ocr_conf": anchor.ocr_conf, "birth_date": fl.get("birth_date"), "birth_place": fl.get("birth_place")}, g)
    claim = fields.model_dump()
    if anchor is not None and c1.status == "pass":  # accreditation is evaluated at the deliberation date (as in C3)
        claim["session_date"] = _m(anchor).get("deliberation_date")
    claim.update({"diploma_id": f"CLAIM-{fields.serial or 'NA'}", "signatory_id": sig_id,
                  "verified_against": [entry_id] if entry_id else []})
    mapping.diploma_to_rdf(claim, g)
    for r in ctx.reg:  # registry copies: same serial held by another holder triggers the uniqueness shape
        rs = row_serial(r.fields or {})
        if rs and rs == "".join((fields.serial or "").split()).upper():
            mapping.diploma_to_rdf({"diploma_id": f"REG-{r.chunk_id}", "serial": rs, "holder_name": row_name(r.fields or {}),
                                    "institution_id": inst, "programme_id": prog, "session_year": fields.session_year}, g)
    _, report = mapping.validate(g)
    claim_iri = str(mapping.iri("diploma", claim["diploma_id"]))
    viol = sorted({r["check_id"] for r in report if r["focus_node"] == claim_iri and r["check_id"] in
                   ("C2", "C3", "C4", "C5", "C6", "C7")})
    py_fail = sorted(c.check_id for c in checks if c.status == "fail" and c.check_id in ("C2", "C3", "C4", "C5", "C6", "C7"))
    return {"shacl_violations": viol, "python_failures": py_fail, "agree": viol == py_fail}
