"""ReporterAgent (Y.3172 node D): bilingual explanations, minimal/redacted outputs, evidence pack (JSON + PDF) with SHA-256.

Version B (governed): neutral templated wording (never accusatory), outputs minimised per PR-REDACT-001 /
PR-CROSSBORDER-001, evidence citations (document, page, row, bbox, crop_url).
Version A (ungoverned): naive wording that may say "FAKE" and returns the full record - intentional, for the comparison.
"""
from __future__ import annotations

import io
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.agents.base import Agent, RunContext
from app.core.config import get_settings
from app.core.hashing import canonical_json, sha256_obj
from app.llm.gateway import LLMGateway, has_forbidden, load_templates, tpl

log = logging.getLogger("veridiplo.reporter")

# full-record personal fields (names follow context_schema.yaml `output.fields`)
FULL_FIELDS = ["holder_name", "date_of_birth", "place_of_birth", "average", "mention", "matricule"]
CHECK_FIELD_LABEL = {"C1": "holder_name", "C2": "birth_date", "C3": "programme_id", "C4": "mention", "C5": "session_year",
                     "C6": "signatory_name", "C7": "serial"}


# ---------------------------------------------------------------------------------------------------------------
# institution lookup (synthetic institutions.json)
# ---------------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=2)
def _institutions(data_dir: str) -> dict[str, dict]:
    for p in (Path(data_dir) / "synthetic" / "institutions.json", Path(data_dir) / "sample" / "institutions.json"):
        if p.exists():
            return {i["id"]: i for i in json.loads(p.read_text(encoding="utf-8"))}
    return {}


def institution_info(institution_id: Optional[str], programme_id: Optional[str], lang: str = "fr") -> dict[str, str]:
    inst = _institutions(get_settings().DATA_DIR).get(institution_id or "", {})
    prog = next((p for p in inst.get("programmes", []) if p["id"] == programme_id), {})
    suffix = "fr" if lang == "fr" else "en"
    return {"institution": inst.get(f"name_{suffix}") or institution_id or "?",
            "programme": prog.get(f"name_{suffix}") or programme_id or "?", "level": prog.get("level") or "?"}


def _fmt_conf(c: Optional[float]) -> str:
    return "n/a" if c is None else f"{c:.2f}"


class ReporterAgent(Agent):
    name = "ReporterAgent"
    node = "D"
    action = "build_report"

    # ---- evidence helpers ---------------------------------------------------------------------------------
    @staticmethod
    def evidence_refs(ctx: RunContext) -> list[dict[str, Any]]:
        """Citations only (no snippet / marks): document, page, row, bbox, crop."""
        return [{"evidence_id": e.evidence_id, "kind": e.kind, "source_file": e.source_file, "sha256": e.sha256,
                 "page": e.page, "row": e.row_idx, "bbox": e.bbox, "crop_url": e.crop_url,
                 "supports": e.supports, "contradicts": e.contradicts} for e in ctx.evidence]

    @staticmethod
    def evidence_count(ctx: RunContext) -> int:
        ids = {i for c in ctx.checks for i in c.evidence_ids}
        ids |= {e.evidence_id for e in ctx.evidence if e.supports or e.contradicts}
        return len(ids)

    @staticmethod
    def failing_fields(ctx: RunContext, lang: str) -> str:
        labels = load_templates(lang)["fields"]
        fields = [CHECK_FIELD_LABEL.get(c.check_id, c.field) for c in ctx.checks if c.status == "fail"]
        fields = list(dict.fromkeys(fields)) or ["mention"]
        # C4 covers average + mention
        out = []
        for f in fields:
            if f == "mention":
                out += ["average", "mention"]
            else:
                out.append(f)
        return ", ".join(labels.get(f, f) for f in dict.fromkeys(out))

    def matched_label(self, ctx: RunContext) -> str:
        best = max(ctx.evidence, key=lambda e: e.score, default=None)
        if not best:
            return "-"
        f = best.fields or {}
        return str(f.get("name_raw") or f.get("holder_name") or best.snippet[:80] or best.evidence_id)

    # ---- texts ---------------------------------------------------------------------------------------------
    def naive_text(self, ctx: RunContext, lang: str) -> str:
        label = ctx.verdict.label if ctx.verdict else "INSUFFICIENT_EVIDENCE"
        return tpl(lang, f"naive.{label}", fields_txt=self.failing_fields(ctx, lang), matched=self.matched_label(ctx))

    def neutral_text(self, ctx: RunContext, lang: str, llm: Optional[LLMGateway] = None) -> str:
        v = ctx.verdict
        label = v.label if v else "INSUFFICIENT_EVIDENCE"
        n = self.evidence_count(ctx)
        reasons = v.reasons_fr if (v and lang == "fr") else (v.reasons_en if v else [])
        reason = " ".join(r for r in reasons if not has_forbidden(r)) or (
            "aucune pièce d'archive correspondante n'a été trouvée." if lang == "fr" else "no matching archive record was found.")
        if v and v.needs_human and label != "INCONSISTENT":
            text = tpl(lang, "explain.UNCERTAIN", reason=reason)
        elif label == "CONSISTENT":
            text = tpl(lang, "explain.CONSISTENT", n_evidence=n, confidence=_fmt_conf(v.confidence if v else None))
        elif label == "INCONSISTENT":
            text = tpl(lang, "explain.INCONSISTENT", fields_txt=self.failing_fields(ctx, lang), n_evidence=n)
        else:
            text = tpl(lang, "explain.INSUFFICIENT_EVIDENCE", reason=reason)
        cites = " ".join(f"[{e.evidence_id}]" for e in ctx.evidence if e.supports or e.contradicts)
        if cites and label in ("CONSISTENT", "INCONSISTENT"):
            text = f"{text} {cites}"
        if llm is not None and llm.provider != "none":
            text = llm.rephrase(text, lang, holder_name=ctx.fields.holder_name if ctx.fields else None,
                                must_keep=[f"[{e.evidence_id}]" for e in ctx.evidence if e.supports or e.contradicts])
        return text

    def rectification(self, ctx: RunContext, lang: str) -> str:
        info = institution_info(ctx.fields.institution_id if ctx.fields else None, None, lang)
        return tpl(lang, "rectification", institution=info["institution"])

    def holder_notice(self, ctx: RunContext, lang: str) -> str:
        info = institution_info(ctx.fields.institution_id if ctx.fields else None, None, lang)
        return tpl(lang, "holder_notice", institution=info["institution"],
                   session_year=ctx.fields.session_year if ctx.fields else "?")

    def archive_request(self, ctx: RunContext) -> str:
        """Bilingual (FR then EN) letter draft to the issuing institution's archive service."""
        f = ctx.fields
        parts = []
        for lang in ("fr", "en"):
            info = institution_info(f.institution_id if f else None, f.programme_id if f else None, lang)
            parts.append(tpl(lang, "archive_request", institution=info["institution"], programme=info["programme"],
                             session_year=f.session_year if f else "?", serial=(f.serial if f else None) or "-",
                             case_id=ctx.case_id))
        return "\n\n---\n\n".join(parts)

    # ---- record / outputs ----------------------------------------------------------------------------------
    def full_record(self, ctx: RunContext) -> dict[str, Any]:
        """Everything known about the holder (what the ungoverned Version A hands out)."""
        f = ctx.fields
        best = max((e for e in ctx.evidence if e.kind in ("pv_row", "registry_row")), key=lambda e: e.score, default=None)
        pv = best.fields if best else {}
        rec: dict[str, Any] = {
            "holder_name": f.holder_name if f else None, "date_of_birth": f.birth_date if f else None,
            "place_of_birth": f.birth_place if f else None, "birth_date": f.birth_date if f else None,
            "birth_place": f.birth_place if f else None,
            "average": (pv.get("average") if pv.get("average") is not None else (f.average if f else None)),
            "mention": pv.get("mention") or (f.mention if f else None),
            "matricule": pv.get("matricule"), "serial": f.serial if f else None,
            "institution_id": f.institution_id if f else None, "programme_id": f.programme_id if f else None,
            "session_year": f.session_year if f else None,
            "matched_pv_row": ({"source_file": best.source_file, "page": best.page, "row": best.row_idx, "fields": best.fields,
                                "snippet": best.snippet} if best else None),
        }
        return rec

    def draft_fields(self, ctx: RunContext) -> list[str]:
        rec = self.full_record(ctx)
        return [k for k in FULL_FIELDS if rec.get(k) not in (None, "")]

    def unconstrained_draft(self, ctx: RunContext) -> dict[str, Any]:
        """First draft as an unconstrained generator would write it (naive wording, full record). It is screened by the
        PolicyGuardian at `before_report`; in Version B a forbidden-wording block triggers the neutral rewrite."""
        text_fr, text_en = self.naive_text(ctx, "fr"), self.naive_text(ctx, "en")
        return {"text_fr": text_fr, "text_en": text_en, "text": f"{text_fr}\n{text_en}", "fields": self.draft_fields(ctx),
                "scope": "full_record"}

    def neutral_draft(self, ctx: RunContext, llm: Optional[LLMGateway] = None) -> dict[str, Any]:
        text_fr, text_en = self.neutral_text(ctx, "fr", llm), self.neutral_text(ctx, "en", llm)
        return {"text_fr": text_fr, "text_en": text_en, "text": f"{text_fr}\n{text_en}", "fields": self.draft_fields(ctx),
                "scope": "full_record"}

    def minimal_output(self, ctx: RunContext, draft: dict[str, Any]) -> dict[str, Any]:
        """Redacted verifier output (PR-REDACT-001): verdict, institution, diploma, session, evidence references."""
        f = ctx.fields
        info = institution_info(f.institution_id if f else None, f.programme_id if f else None, ctx.request.lang)
        return {"scope": "evidence_pack", "audience": "verifier", "verdict": ctx.verdict.label if ctx.verdict else None,
                "institution": info["institution"], "programme": info["programme"], "programme_level": info["level"],
                "session_year": f.session_year if f else None, "evidence_refs": self.evidence_refs(ctx),
                "explanation_fr": draft["text_fr"], "explanation_en": draft["text_en"]}

    def attestation(self, ctx: RunContext) -> dict[str, Any]:
        """Cross-border minimal attestation: yes/no + hash (PR-CROSSBORDER-001)."""
        f = ctx.fields
        info = institution_info(f.institution_id if f else None, f.programme_id if f else None, ctx.request.lang)
        label = ctx.verdict.label if ctx.verdict else None
        answer = {"CONSISTENT": "yes", "INCONSISTENT": "no"}.get(label or "", "undetermined")
        core = {"case_id": ctx.case_id, "answer": answer, "institution": info["institution"],
                "session_year": f.session_year if f else None, "serial": f.serial if f else None}
        att_hash = sha256_obj(core)
        ans_fr = {"yes": "oui", "no": "non", "undetermined": "indéterminée"}[answer]
        out = {"scope": "attestation", "audience": "third_country_verifier", "verdict": label, "consistent": answer,
               "attestation_hash": att_hash, "institution": info["institution"], "programme_level": info["level"],
               "session_year": f.session_year if f else None, "transfer_flag": True,
               "explanation_fr": tpl("fr", "explain.ATTESTATION", answer=ans_fr, institution=info["institution"],
                                     session_year=core["session_year"], hash=att_hash[:16]),
               "explanation_en": tpl("en", "explain.ATTESTATION", answer=answer, institution=info["institution"],
                                     session_year=core["session_year"], hash=att_hash[:16])}
        return out

    def naive_output(self, ctx: RunContext, draft: dict[str, Any]) -> dict[str, Any]:
        """Version A: full record, evidence snippets, naive text."""
        rec = self.full_record(ctx)
        return {"scope": "full_record", "audience": "verifier", "verdict": ctx.verdict.label if ctx.verdict else None,
                "record": rec, **{k: rec[k] for k in ("average", "mention", "birth_date", "birth_place", "matricule")},
                "evidence": [e.model_dump() for e in ctx.evidence], "evidence_crops": [e.crop_url for e in ctx.evidence],
                "explanation_fr": draft["text_fr"], "explanation_en": draft["text_en"]}

    def held_output(self, ctx: RunContext, reason_fr: str, reason_en: str) -> dict[str, Any]:
        return {"scope": "none", "status": "pending_review", "explanation_fr": tpl("fr", "explain.PENDING"),
                "explanation_en": tpl("en", "explain.PENDING"), "reason_fr": reason_fr, "reason_en": reason_en}

    def blocked_output(self, messages_fr: list[str], messages_en: list[str]) -> dict[str, Any]:
        return {"scope": "none", "status": "blocked",
                "explanation_fr": tpl("fr", "explain.BLOCKED", messages=" ".join(messages_fr)),
                "explanation_en": tpl("en", "explain.BLOCKED", messages=" ".join(messages_en))}

    # ---- evidence pack -------------------------------------------------------------------------------------
    def build_pack(self, result: dict[str, Any], audience: str = "registrar", policy_texts: Optional[dict] = None) -> dict[str, Any]:
        """Evidence pack (JSON) from a CaseResult dump. `report_sha256` = sha256 of canonical JSON of the pack body."""
        body: dict[str, Any] = {
            "specimen": "SPECIMEN - SYNTHETIC DATA", "case_id": result["case_id"], "policy_mode": result["policy_mode"],
            "audience": audience, "status": result["status"], "verdict": result.get("verdict"),
            "explanation_fr": result.get("explanation_fr"), "explanation_en": result.get("explanation_en"),
            "checks": result.get("checks", []), "evidence": result.get("evidence", []),
            "policy": {"fired_rules": result.get("fired_rules", []), "decisions": [
                {"checkpoint": d["checkpoint"], "action": d["action"], "rules": [f["rule_id"] for f in d["fired"]]}
                for d in result.get("decisions", [])], "kb_chunks": policy_texts or {}},
            "gaps": result.get("gaps", []), "steps": result.get("steps", []),
            "shared_output": result.get("shared_output", {}) if audience == "verifier" else None,
        }
        if audience == "verifier":  # minimal pack: drop registrar-only parts
            body["evidence"] = result.get("shared_output", {}).get("evidence_refs", [])
            body["checks"] = [{"check_id": c["check_id"], "status": c["status"]} for c in result.get("checks", [])]
        body["report_sha256"] = sha256_obj({k: v for k, v in body.items() if k != "report_sha256"})
        return body

    def render_pdf(self, pack: dict[str, Any], lang: str = "fr") -> bytes:
        """PDF evidence pack (reportlab). Contains the SHA-256 of the JSON pack for integrity checking."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape

        st = getSampleStyleSheet()
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, title=f"VeriDiplo {pack['case_id']}")

        def P(t: str, style: str = "BodyText") -> Paragraph:
            return Paragraph(escape(str(t)).replace("\n", "<br/>"), st[style])

        fr = lang == "fr"
        v = pack.get("verdict") or {}
        story: list[Any] = [
            P("VeriDiplo - SPECIMEN - SYNTHETIC DATA", "Title"),
            P(("Dossier de preuves" if fr else "Evidence pack") + f" - {pack['case_id']}", "Heading2"),
            P(f"policy_mode={pack['policy_mode']} | status={pack['status']} | audience={pack['audience']}"),
            P(("Verdict : " if fr else "Verdict: ") + str(v.get("label") if isinstance(v, dict) else v)
              + (f" (confiance {v.get('confidence')})" if isinstance(v, dict) and v.get("confidence") is not None else "")),
            Spacer(1, 8), P(pack.get("explanation_fr" if fr else "explanation_en") or ""), Spacer(1, 8),
        ]
        if pack.get("checks"):
            rows = [["ID", "Champ" if fr else "Field", "Statut" if fr else "Status", "Preuves" if fr else "Evidence"]]
            for c in pack["checks"]:
                rows.append([c["check_id"], c.get("field", ""), c["status"], ",".join(c.get("evidence_ids", []) or [])])
            t = Table(rows, hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
            story += [P("Contrôles de cohérence" if fr else "Consistency checks", "Heading3"), t, Spacer(1, 8)]
        if pack.get("evidence"):
            rows = [["ID", "Document", "Page", "Ligne" if fr else "Row", "BBox"]]
            for e in pack["evidence"]:
                rows.append([e.get("evidence_id", ""), str(e.get("source_file") or "")[-40:], e.get("page") or "",
                             e.get("row_idx", e.get("row")) if e.get("row_idx", e.get("row")) is not None else "",
                             ",".join(f"{x:.0f}" for x in (e.get("bbox") or []))])
            t = Table(rows, hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
            story += [P("Pièces citées" if fr else "Cited evidence", "Heading3"), t, Spacer(1, 8)]
        fired = pack.get("policy", {}).get("fired_rules", [])
        if fired:
            story.append(P("Règles de politique appliquées" if fr else "Policy rules applied", "Heading3"))
            chunks = pack["policy"].get("kb_chunks", {})
            for r in fired:
                story.append(P(f"{r['rule_id']} [{r['action']}] - {r['message_fr' if fr else 'message_en']}"))
                for cid in r.get("kb_chunk_ids", []):
                    c = chunks.get(cid)
                    story.append(P(f"  > {cid}" + (f" ({c.get('title')}, {c.get('article')})" if c else ""), "Italic"))
        if pack.get("gaps"):
            story.append(P("Écarts de politique" if fr else "Policy gaps", "Heading3"))
            for g in pack["gaps"]:
                story.append(P(f"{g.get('gap_id')}: {g.get('title')}"))
        story += [Spacer(1, 12), P(f"SHA-256: {pack['report_sha256']}", "Code")]
        doc.build(story)
        return buf.getvalue()

    def run(self, ctx: RunContext) -> RunContext:  # pipeline stage D is orchestrated explicitly
        return ctx
