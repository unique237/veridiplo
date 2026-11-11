"""`make seed`: generate synthetic data (if missing), load KB + policy rules + gap register, load institutions and
PV entries into the KG, ingest all archives (OCR -> chunks -> embeddings) and print a summary.

    python -m scripts.seed [--workers N] [--skip-data] [--skip-kb] [--skip-kg] [--skip-ingest] [--no-embed]
                           [--reset-archive] [--data-dir DIR]

Idempotent: documents are deduplicated by SHA-256, KB rows are upserted, RDF is a set, OCR results are cached as JSON
under DATA_DIR/cache/ocr/ so a second run takes seconds.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("veridiplo.seed")
REPO = Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    from app.core.config import get_settings

    return Path(os.environ.get("DATA_DIR") or get_settings().DATA_DIR)


def _kb_dir() -> Path:
    from app.core.config import get_settings

    return Path(os.environ.get("KB_DIR") or get_settings().KB_DIR)


# ----------------------------------------------------------------------------------------------- data
def ensure_data(data_dir: Path) -> bool:
    """Run the generator when data/synthetic is missing. Returns True if it generated."""
    if (data_dir / "synthetic" / "institutions.json").is_file() and (data_dir / "synthetic" / "pv").is_dir():
        return False
    print("[seed] data/synthetic missing -> python -m data_gen", flush=True)
    env = {**os.environ, "DATA_DIR": str(data_dir)}
    subprocess.run([sys.executable, "-m", "data_gen"], cwd=str(REPO), env=env, check=True)
    return True


# ----------------------------------------------------------------------------------------------- KB
KB_FIELDS = ("chunk_id", "source_id", "title", "issuer", "date", "jurisdiction", "binding", "kind", "article",
             "as_reported", "lang", "topics", "pipeline_nodes", "readiness_dims", "url", "text", "sha256")


def load_kb(session, embed: bool = True) -> dict:
    """kb/chunks/*.jsonl -> KBChunk ORM rows (upsert), embeddings via embed_passages; rules -> PolicyRuleRow."""
    from app.core.hashing import sha256_bytes
    from app.core.models import KBChunk, PolicyRuleRow

    kb = _kb_dir()
    rows: list[dict] = []
    for p in sorted((kb / "chunks").glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    existing = {r.chunk_id: r for r in session.query(KBChunk).all()}
    objs: list[KBChunk] = []
    n_new = 0
    for d in rows:
        vals = {k: d.get(k) for k in KB_FIELDS}
        vals["sha256"] = vals["sha256"] or sha256_bytes((d.get("text") or "").encode())
        vals["binding"] = bool(vals["binding"])
        vals["as_reported"] = bool(vals["as_reported"])
        for k in ("topics", "pipeline_nodes", "readiness_dims"):
            vals[k] = list(vals[k] or [])
        vals["kind"] = vals["kind"] or "primary"
        o = existing.get(d["chunk_id"])
        if o is None:
            o = KBChunk(**vals)
            session.add(o)
            n_new += 1
        else:
            changed = o.sha256 != vals["sha256"]
            for k, v in vals.items():
                setattr(o, k, v)
            if changed:
                o.embedding = None
        objs.append(o)
    session.flush()
    n_emb = 0
    if embed:
        try:
            from app.search.embed import embed_passages

            todo = [o for o in objs if o.embedding is None]
            for i in range(0, len(todo), 64):
                batch = todo[i:i + 64]
                for o, v in zip(batch, embed_passages([o.text[:2000] for o in batch])):
                    o.embedding = v
                n_emb += len(batch)
        except Exception as exc:
            log.warning("KB embeddings skipped: %s", exc)
    # policy rules
    from app.policy import loader

    loader.clear_cache()
    n_rules = 0
    for r in loader.load_rules(kb):
        body = json.loads(json.dumps(r, default=str))
        row = session.get(PolicyRuleRow, r["id"])
        if row is None:
            session.add(PolicyRuleRow(id=r["id"], checkpoint=r["checkpoint"], action=r["action"], body=body))
        else:
            row.checkpoint, row.action, row.body = r["checkpoint"], r["action"], body
        n_rules += 1
    n_gaps = 0
    try:
        from app.agents.gap_analyst import seed_gaps

        n_gaps = seed_gaps(session)
    except Exception as exc:
        log.warning("seed gap register skipped: %s", exc)
    session.commit()
    return {"kb_chunks": len(rows), "kb_new": n_new, "kb_embedded": n_emb, "rules": n_rules, "gaps_new": n_gaps}


# ----------------------------------------------------------------------------------------------- KG
def institution_dicts(data_dir: Path) -> list[dict]:
    """institutions.json -> the dict contract of ``app.kg.mapping.institution_to_rdf``."""
    p = data_dir / "synthetic" / "institutions.json"
    out = []
    for i in json.loads(p.read_text(encoding="utf-8")):
        out.append({
            "institution_id": i["id"], "name": i.get("name_fr"), "name_en": i.get("name_en"),
            "language": i.get("language", "fr"), "private": i.get("kind") == "private",
            "serial_pattern": i.get("serial_pattern"),
            "programmes": [{"programme_id": pr["id"], "name": pr.get("name_fr"), "name_en": pr.get("name_en"),
                            "level": pr.get("level"), "accreditations": pr.get("accreditations", [])}
                           for pr in i.get("programmes", [])],
            "signatories": [{"signatory_id": s["id"], "name": s["name"], "office": s.get("title"),
                             "in_office_from": s.get("in_office_from"), "in_office_to": s.get("in_office_to")}
                            for s in i.get("signatories", [])],
        })
    return out


def load_kg_institutions(data_dir: Path) -> int:
    from app.kg import mapping

    g = None
    for inst in institution_dicts(data_dir):
        g = mapping.institution_to_rdf(inst, g)
    return mapping.load_into_kg(g)


def load_kg_entries(session) -> int:
    """All PV rows (pv_row chunks) -> vd:DeliberationEntry triples."""
    from sqlalchemy import select

    from app.agents.extraction import entries_to_rdf
    from app.core.models import Chunk, SourceDocument
    from app.kg import mapping

    total = 0
    docs = session.execute(select(SourceDocument).where(SourceDocument.doc_type == "pv",
                                                         SourceDocument.status == "ingested")).scalars().all()
    g = None
    for i, d in enumerate(docs, 1):
        chunks = session.execute(select(Chunk).where(Chunk.document_id == d.id, Chunk.kind == "pv_row")).scalars().all()
        g = entries_to_rdf(chunks, d, g)
        if g is not None and (i % 40 == 0 or i == len(docs)):
            total += mapping.load_into_kg(g)
            g = None
    return total


# ----------------------------------------------------------------------------------------------- evaluation
def eval_pv_recall(session, data_dir: Path) -> dict:
    """Recall of OCR'd PV rows against data/synthetic/pv_index.json (TEST-ONLY ground truth)."""
    from sqlalchemy import select

    from app.core.models import Chunk, SourceDocument
    from app.core.names import name_key
    from app.core.normalize import normalize_decision, normalize_mention, parse_average

    p = data_dir / "synthetic" / "pv_index.json"
    if not p.is_file():
        return {}
    idx = json.loads(p.read_text(encoding="utf-8"))
    docs = {d.filename: d for d in session.execute(select(SourceDocument).where(SourceDocument.doc_type == "pv")).scalars()}
    tot = dict(rows=0, found=0, name=0, matricule=0, average=0, mention=0, decision=0, all=0)
    by_preset: dict[str, dict] = {}
    for key, v in idx.items():
        d = docs.get(f"{key}.pdf")
        got = {}
        if d is not None:
            for c in session.execute(select(Chunk).where(Chunk.document_id == d.id, Chunk.kind == "pv_row")).scalars():
                got[((c.fields or {}).get("matricule") or "").upper()] = c
        preset = v.get("scan_preset", "?")
        bp = by_preset.setdefault(preset, dict(rows=0, found=0, name=0, all=0))
        for r in v["rows"]:
            c = got.get((r["matricule"] or "").upper())
            tot["rows"] += 1
            bp["rows"] += 1
            if c is None:
                continue
            f = c.fields or {}
            ok_name = name_key(f.get("name_raw")) == name_key(r["name"])
            ok_avg = (parse_average(r["average"]) == f.get("average"))
            ok_ment = (normalize_mention(r["mention"]) == f.get("mention"))
            ok_dec = (normalize_decision(r["decision"]) == f.get("decision"))
            tot["found"] += 1
            bp["found"] += 1
            tot["matricule"] += 1
            tot["name"] += ok_name
            tot["average"] += ok_avg
            tot["mention"] += ok_ment
            tot["decision"] += ok_dec
            allok = ok_name and ok_avg and ok_ment and ok_dec
            tot["all"] += allok
            bp["name"] += ok_name
            bp["all"] += allok
    n = max(tot["rows"], 1)
    out = {"rows_truth": tot["rows"], "row_recall": tot["found"] / n}
    for k in ("name", "average", "mention", "decision", "all"):
        out[f"{k}_recall"] = tot[k] / n
    out["by_scan_preset"] = {k: {"rows": b["rows"], "row_recall": b["found"] / max(b["rows"], 1),
                                 "name_recall": b["name"] / max(b["rows"], 1), "all_recall": b["all"] / max(b["rows"], 1)}
                             for k, b in by_preset.items()}
    return out


# ----------------------------------------------------------------------------------------------- main
def summary(session) -> list[tuple[str, str]]:
    from sqlalchemy import func, select

    from app.core.models import Chunk, KBChunk, PolicyRuleRow, SourceDocument

    rows: list[tuple[str, str]] = []
    for t, n in session.execute(select(SourceDocument.doc_type, func.count()).group_by(SourceDocument.doc_type)).all():
        rows.append((f"documents[{t}]", str(n)))
    for t, n in session.execute(select(SourceDocument.status, func.count()).group_by(SourceDocument.status)).all():
        rows.append((f"documents status={t}", str(n)))
    for k, n in session.execute(select(Chunk.kind, func.count()).group_by(Chunk.kind)).all():
        rows.append((f"chunks[{k}]", str(n)))
    rows.append(("chunks embedded", str(session.execute(select(func.count()).select_from(Chunk).where(Chunk.embedding.is_not(None))).scalar_one())))
    rows.append(("kb_chunks", str(session.execute(select(func.count()).select_from(KBChunk)).scalar_one())))
    rows.append(("policy_rules", str(session.execute(select(func.count()).select_from(PolicyRuleRow)).scalar_one())))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2))))
    ap.add_argument("--skip-data", action="store_true")
    ap.add_argument("--skip-kb", action="store_true")
    ap.add_argument("--skip-kg", action="store_true")
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--reset-archive", action="store_true", help="delete ingested documents/chunks first")
    ap.add_argument("--data-dir", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if a.data_dir:
        os.environ["DATA_DIR"] = a.data_dir
    sys.path.insert(0, str(REPO))

    from app.core.db import SessionLocal, init_db

    t0 = time.time()
    data_dir = _data_dir()
    timings: dict[str, float] = {}
    init_db()
    if not a.skip_data:
        t = time.time()
        ensure_data(data_dir)
        timings["data"] = time.time() - t
    s = SessionLocal()
    try:
        if not a.skip_kb:
            t = time.time()
            print("[seed] KB chunks + policy rules ->", load_kb(s, embed=not a.no_embed), flush=True)
            timings["kb"] = time.time() - t
        if not a.skip_kg:
            t = time.time()
            try:
                n = load_kg_institutions(data_dir)
                print(f"[seed] KG: institutions/programmes/signatories loaded ({n} triples)", flush=True)
            except Exception as exc:
                print(f"[seed] KG institutions skipped: {type(exc).__name__}: {exc}", flush=True)
            timings["kg_inst"] = time.time() - t
        if not a.skip_ingest:
            from sqlalchemy import delete

            from app.agents.intake import IntakeAgent
            from app.core.models import Chunk, SourceDocument

            if a.reset_archive:
                s.execute(delete(Chunk))
                s.execute(delete(SourceDocument))
                s.commit()
            t = time.time()
            root = data_dir / "synthetic"
            docs = []
            for sub in ("pv", "registries", "lists"):
                if (root / sub).is_dir():
                    docs += IntakeAgent(workers=a.workers, embed=not a.no_embed).ingest_directory(str(root / sub))
            timings["ingest"] = time.time() - t
            print(f"[seed] ingested/seen {len(docs)} documents in {timings['ingest']:.0f}s", flush=True)
        if not a.skip_kg and not a.skip_ingest:
            t = time.time()
            try:
                print(f"[seed] KG: {load_kg_entries(s)} PV-entry triples loaded", flush=True)
            except Exception as exc:
                print(f"[seed] KG entries skipped: {type(exc).__name__}: {exc}", flush=True)
            timings["kg_entries"] = time.time() - t
        print("\n=== SEED SUMMARY ===")
        for k, v in summary(s):
            print(f"  {k:<28} {v}")
        rec = eval_pv_recall(s, data_dir)
        if rec:
            print("\n=== PV extraction vs pv_index.json (test-only truth) ===")
            for k in ("rows_truth", "row_recall", "name_recall", "average_recall", "mention_recall", "decision_recall",
                      "all_recall"):
                v = rec[k]
                print(f"  {k:<18} {v:.3f}" if isinstance(v, float) else f"  {k:<18} {v}")
            for k, b in rec["by_scan_preset"].items():
                print(f"  preset={k:<6} rows={b['rows']:<5} row_recall={b['row_recall']:.3f} "
                      f"name_recall={b['name_recall']:.3f} all_fields={b['all_recall']:.3f}")
        print(f"\n[seed] done in {time.time() - t0:.0f}s  " + " ".join(f"{k}={v:.0f}s" for k, v in timings.items()))
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
