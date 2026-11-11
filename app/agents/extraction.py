"""ExtractionAgent (Y.3172 PP): diploma image / typed JSON -> ``DiplomaFields`` (rules + regex first, LLM fallback).

* ``extract_diploma_fields(path, lang_hint)`` OCRs the diploma (Tesseract fra+eng, RapidOCR fallback) and applies
  label-driven regex rules (FR and EN templates). A sibling ``<stem>.json`` is never read here (it is ground truth).
* ``fields_from_json(path)`` loads typed fields (JSON file / dict) for the typed-input path.
* The LLM is only consulted when ``LLM_PROVIDER != none`` and a critical field is still missing.
* ``entries_to_rdf`` / ``diploma_to_rdf`` emit RDF through ``app.kg.mapping`` (coordination with kg-ontology).

Normalisation conventions: mention -> canonical French label (Passable, Assez Bien, Bien, Très Bien; English labels are
mapped), dates -> ISO-8601, average -> float (comma decimals accepted), serial -> upper-case without spaces.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.agents import ocr as ocr_mod
from app.core import refdata
from app.core.hashing import sha256_file
from app.core.normalize import (clean_serial, normalize_mention, parse_average, parse_date, parse_session_year)
from app.core.schemas import DiplomaFields

log = logging.getLogger("veridiplo.extraction")

CRITICAL = ("holder_name", "institution_id", "session_year", "average", "mention", "serial")

_CONFUSABLE_DIGIT = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8", "Z": "2",
                                   "D": "0", "Q": "0"})


# ------------------------------------------------------------------------------------------------ typed input
def fields_from_json(path_or_dict: str | Path | dict[str, Any]) -> DiplomaFields:
    """Typed diploma fields from a JSON file (or dict). Unknown keys are ignored."""
    if isinstance(path_or_dict, dict):
        raw = path_or_dict
    else:
        with open(path_or_dict, encoding="utf-8") as f:
            raw = json.load(f)
    allowed = set(DiplomaFields.model_fields)
    data = {k: v for k, v in raw.items() if k in allowed}
    if "case_id" not in data and raw.get("case_id"):
        data["case_id"] = raw["case_id"]
    if data.get("birth_date"):
        data["birth_date"] = parse_date(data["birth_date"]) or data["birth_date"]
    if data.get("issue_date"):
        data["issue_date"] = parse_date(data["issue_date"]) or data["issue_date"]
    if data.get("average") is not None:
        data["average"] = parse_average(data["average"])
    if data.get("session_year") is not None:
        data["session_year"] = parse_session_year(data["session_year"])
    if data.get("mention"):
        data["mention"] = normalize_mention(data["mention"]) or data["mention"]
    if data.get("serial"):
        data["serial"] = clean_serial(data["serial"]) or data["serial"]
    return DiplomaFields(**data)


# ------------------------------------------------------------------------------------------------ OCR text rules
def _after(label_re: str, text: str, flags: int = re.I) -> Optional[str]:
    m = re.search(label_re + r"\s*[:;]?\s*(.+)$", text, flags | re.M)
    return m.group(1).strip() if m else None


def _strict_serial_regex(inst: dict[str, Any] | None, programme_id: str | None) -> str | None:
    """Exact regex built from the institution's ``serial_pattern`` (e.g. ``UFN/{YYYY}/{PROG}/{NNNNN}``)."""
    pat = (inst or {}).get("serial_pattern")
    if not pat:
        return None
    out = ""
    for part in re.split(r"(\{[A-Z]+\})", pat):
        if part == "{YYYY}":
            out += r"\d{4}"
        elif part == "{PROG}":
            out += re.escape(programme_id) if programme_id else r"[A-Z0-9]{2,10}"
        elif re.fullmatch(r"\{N+\}", part):
            out += r"\d{%d}" % (len(part) - 2)
        else:
            out += re.escape(part)
    return "^" + out + "$"


def _repair_serial(raw: str, institution_id: str | None, programme_id: str | None = None) -> str:
    """Fix O/0, I/1 ... confusions and one spurious character using the institution's serial pattern/format."""
    s = clean_serial(raw) or ""
    inst = refdata.get_institution(institution_id) if institution_id else None
    strict = _strict_serial_regex(inst, programme_id)
    pat = inst.get("serial_format") if inst else None
    if strict and re.match(strict, s):
        return s
    if not strict and (not pat or re.match(pat, s)):
        return s

    def fix(m: re.Match) -> str:
        return m.group(0).translate(_CONFUSABLE_DIGIT)

    base = [s, re.sub(r"[0-9OIlSBZDQ]{3,}", fix, s).upper(), s.replace("O", "0"), s.replace("I", "1")]
    cands = list(base)
    for c in base:  # one inserted character - only OCR-typical noise glyphs, never a digit (no masking of tampering)
        cands += [c[:i] + c[i + 1:] for i in range(len(c)) if c[i] in "OIlSBZDQ|"]
    for cand in cands:
        if (strict and re.match(strict, cand)) or (not strict and pat and re.match(pat, cand)):
            return cand
    return s


def parse_diploma_text(lines: list[str], lang_hint: str | None = None) -> DiplomaFields:
    """Rule-based field extraction from the OCR'd lines of a diploma (pure function, unit-testable)."""
    text = "\n".join(lines)
    en = bool(re.search(r"DIPLOMA OF|hereby confers|Overall average|Diploma No|Born on", text, re.I))
    fr = bool(re.search(r"DIPL[ÔO]ME DE|d[ée]cerne|Moyenne g[ée]n|N[°o] du dipl|N[ée]\(e\)", text, re.I))
    lang = "en" if (en and not fr) else "fr" if (fr and not en) else (lang_hint or ("en" if en else "fr"))
    f: dict[str, Any] = {"language": lang}

    # --- institution (header) ------------------------------------------------------------------
    inst, _ = refdata.match_institution(" ".join(lines[:6]))
    serial_rest = _after(r"(?:N[°o]\s*du\s*dipl[oô]me|Diploma\s*No\.?|Dipl[oô]me\s*N[°o])", text)
    serial_raw = None
    if serial_rest:
        # OCR may insert blanks inside the number ("00 1 34") or trail stamp noise: join tokens until the
        # institution's serial format matches; otherwise keep the first token
        toks = serial_rest.split()
        serial_raw = toks[0]
        guess, _ = refdata.match_institution(" ".join(lines[:6]))
        pat = (refdata.get_institution(guess) or {}).get("serial_format") if guess else None
        if pat:
            for n in range(1, min(len(toks), 6) + 1):
                if re.match(pat, (clean_serial("".join(toks[:n])) or "")):
                    serial_raw = "".join(toks[:n])
                    break
    if not inst and serial_raw:
        for i in refdata.institutions():
            if clean_serial(serial_raw).startswith(i["id"]):
                inst = i["id"]
                break
    f["institution_id"] = inst

    # --- holder ---------------------------------------------------------------------------------
    name = _after(r"(?:Nom\s*et\s*pr[eé\w]*|Name)", text)
    if name:
        f["holder_name"] = re.sub(r"\s+", " ", name).strip(" .:;_-")

    # --- birth ----------------------------------------------------------------------------------
    m = re.search(r"N[ée]\W*(?:e\W*)?\s*le\s+(.+?)\s+[àa]\s+(.+)$", text, re.I | re.M) or \
        re.search(r"Born\s+on\s+(.+?)\s+in\s+(.+)$", text, re.I | re.M)
    if m:
        f["birth_date"] = parse_date(m.group(1))
        f["birth_place"] = m.group(2).strip(" .:;_|-")

    # --- programme: the line following "le diplôme de" / "the degree of" ------------------------
    prog_txt = None
    for i, ln in enumerate(lines):
        if re.search(r"le\s+dipl[ôo]me\s+de|the\s+degree\s+of", ln, re.I) and i + 1 < len(lines):
            prog_txt = lines[i + 1]
            break
    pid = None
    if prog_txt:
        pid, _ = refdata.match_programme(prog_txt, inst, min_score=75)
    if not pid:  # scan every line
        best = (0.0, None)
        for ln in lines:
            p, s = refdata.match_programme(ln, inst, min_score=88)
            if p and s > best[0]:
                best = (s, p)
        pid = best[1]
    f["programme_id"] = pid

    # --- session / average / mention -------------------------------------------------------------
    m = re.search(r"Session\s+(?:de|of)\s+(?:\S+\s+)?((?:19|20)\d{2})", text, re.I)
    if m:
        f["session_year"] = int(m.group(1))
    m = re.search(r"(\d{1,2}\s*[.,]\s*\d{1,2})\s*/\s*20", text)
    if m:
        f["average"] = parse_average(m.group(1).replace(" ", ""))
    ment = None
    m = re.search(r"(?:Mention|Grade)\s*[:;]\s*(.+)$", text, re.I | re.M)
    if m:
        ment = m.group(1).strip()
    if ment:
        f["mention"] = normalize_mention(ment)

    # --- serial / issue date ---------------------------------------------------------------------
    if serial_raw:
        f["serial"] = _repair_serial(serial_raw, inst, pid)
    m = re.search(r"(?:Fait\s+[àa].+?,\s*le|Done\s+at\s+.+?,\s*on)\s+(.+)$", text, re.I | re.M)
    if m:
        f["issue_date"] = parse_date(m.group(1))

    # --- signatory: snap to the institution's known signatories when the OCR line is close ------
    from rapidfuzz import fuzz
    from unidecode import unidecode

    sigs = [s["name"] for i in refdata.institutions() if not inst or i["id"] == inst for s in i.get("signatories", [])]
    best, score = None, 0.0
    for ln in lines:
        lk = unidecode(ln).upper()
        for nm in sigs:
            sc = fuzz.partial_ratio(unidecode(nm).upper(), lk) if len(ln) >= 8 else 0
            if sc > score:
                best, score = nm, sc
    if best and score >= 88:
        f["signatory_name"] = best
    return DiplomaFields(**{k: v for k, v in f.items() if v is not None or k in ("language",)})


def _llm_fill(lines: list[str], fields: DiplomaFields) -> DiplomaFields:
    """Optional LLM fallback for still-missing critical fields (never used with LLM_PROVIDER=none)."""
    from app.core.config import get_settings

    provider = get_settings().LLM_PROVIDER
    missing = [k for k in CRITICAL if getattr(fields, k) in (None, "")]
    if provider == "none" or not missing:
        return fields
    try:
        from app.llm import gateway

        if provider in ("anthropic", "openai"):
            log.warning("cross-border transfer: sending OCR text of a diploma to %s (Law 2024/017 Art. 32 as reported)",
                        provider)
        prompt = ("Extract these fields from the OCR text of a diploma and answer with a single JSON object only "
                  f"(keys: {', '.join(missing)}; null if absent; dates ISO; average as number).\n\n" + "\n".join(lines))
        out = gateway._complete(provider, prompt)
        m = re.search(r"\{.*\}", out, re.S)
        data = json.loads(m.group(0)) if m else {}
        upd = {k: v for k, v in data.items() if k in missing and v not in (None, "")}
        if "average" in upd:
            upd["average"] = parse_average(upd["average"])
        if "mention" in upd:
            upd["mention"] = normalize_mention(upd["mention"]) or upd["mention"]
        if "serial" in upd:
            upd["serial"] = clean_serial(upd["serial"])
        return fields.model_copy(update=upd)
    except Exception as exc:
        log.warning("LLM fallback failed: %s", exc)
        return fields


def diploma_lines(path: str | Path) -> tuple[list[str], float, str, list[str] | None]:
    """OCR a diploma (image or PDF): (text lines, mean OCR confidence 0..1, engine, lines of the 2nd engine)."""
    ocr = ocr_mod.ocr_document(path, sha=sha256_file(path), workers=1)
    lines: list[str] = []
    alt_lines = None
    for p in ocr["pages"][:1]:
        lines += [ocr_mod.line_text(l) for l in ocr_mod.cluster_lines(p["words"])]
        if p.get("alt"):
            alt_lines = [ocr_mod.line_text(l) for l in ocr_mod.cluster_lines(p["alt"]["words"])]
    return lines, float(ocr.get("conf") or 0.0), (ocr["pages"][0]["engine"] if ocr["pages"] else "none"), alt_lines


def extract_diploma_fields_ex(path: str, lang_hint: str | None = None) -> tuple[DiplomaFields, dict[str, Any]]:
    """Like ``extract_diploma_fields`` but also returns ``{ocr_conf, engine, lines, missing}``."""
    lines, conf, engine, alt_lines = diploma_lines(path)
    fields = parse_diploma_text(lines, lang_hint)
    if alt_lines:  # second OCR engine: only fills fields the primary reading could not extract
        alt = parse_diploma_text(alt_lines, lang_hint)
        fill = {k: v for k, v in alt.model_dump().items() if v not in (None, "") and getattr(fields, k) in (None, "")}
        fields = fields.model_copy(update=fill)
    fields = _llm_fill(lines, fields)
    missing = [k for k in CRITICAL if getattr(fields, k) in (None, "")]
    return fields, {"ocr_conf": conf, "engine": engine, "lines": lines, "missing": missing}


def extract_diploma_fields(path: str, lang_hint: str | None = None) -> DiplomaFields:
    """OCR the diploma image and extract its fields with rules/regex (LLM fallback only if enabled)."""
    return extract_diploma_fields_ex(path, lang_hint)[0]


# ------------------------------------------------------------------------------------------------ RDF
def entries_to_rdf(chunks: list[Any], document: Any = None, graph=None):
    """PV row chunks -> RDF (``app.kg.mapping.entry_to_rdf``). ``document`` supplies sha256/institution/session."""
    from app.kg import mapping

    g = graph
    for c in chunks:
        if c.kind != "pv_row":
            continue
        f = c.fields or {}
        entry = {
            "entry_id": f"{c.document_id}-{c.page}-{c.row_idx}", "document_id": c.document_id, "page": c.page,
            "bbox": list(c.bbox) if c.bbox else None, "matricule": f.get("matricule"), "name_raw": f.get("name_raw"),
            "name_norm": f.get("name_norm"), "average": f.get("average"), "mention": f.get("mention"),
            "decision": f.get("decision"), "institution_id": c.institution_id, "programme_id": c.programme_id,
            "session_year": c.session_year, "ocr_conf": c.ocr_conf,
            "document_sha256": getattr(document, "sha256", None), "deliberation_date": f.get("deliberation_date"),
        }
        g = mapping.entry_to_rdf(entry, g)
    return g


def diploma_to_rdf(fields: DiplomaFields, graph=None):
    from app.kg import mapping

    return mapping.diploma_to_rdf(fields.model_dump(), graph)
