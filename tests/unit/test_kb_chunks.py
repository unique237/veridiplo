"""KB chunk format tests (CLAUDE.md §4.4) and source catalogue honesty rules (§2 rule 1)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.policy import loader  # noqa: E402

REQUIRED = ["chunk_id", "source_id", "title", "issuer", "date", "jurisdiction", "binding", "kind", "article", "as_reported",
            "lang", "topics", "pipeline_nodes", "readiness_dims", "url", "text", "sha256"]


def test_chunk_format():
    chunks = loader.load_chunks()
    assert chunks
    for cid, c in chunks.items():
        for k in REQUIRED:
            assert k in c, f"{cid} missing {k}"
        assert c["chunk_id"].startswith(c["source_id"] + "#")
        assert c["kind"] in ("primary", "summary")
        assert c["lang"] in ("fr", "en")
        assert hashlib.sha256(c["text"].encode("utf-8")).hexdigest() == c["sha256"], cid
        assert c["text"].strip()
        if c["kind"] == "summary":
            assert c["as_reported"] is True, f"{cid}: summary chunks must be as_reported"


def test_chunks_only_from_catalogued_sources():
    ids = {s["id"] for s in loader.load_sources()}
    for c in loader.load_chunks().values():
        assert c["source_id"] in ids


def test_pending_sources_have_no_chunks():
    have = {c["source_id"] for c in loader.load_chunks().values()}
    for s in loader.load_sources():
        if s.get("status") == "pending_download":
            assert s["id"] not in have, f"{s['id']} is pending_download but has chunks (fabricated?)"


def test_every_source_has_status_and_licence():
    for s in loader.load_sources():
        assert s.get("status") in ("downloaded", "summary_only", "pending_download", "pending_ocr"), s["id"]
        if s["status"] != "pending_download":
            assert s.get("licence"), s["id"]


def test_source_count_and_education_and_ai_coverage():
    srcs = loader.load_sources()
    assert len(srcs) >= 25
    have = {c["source_id"] for c in loader.load_chunks().values()}
    edu = {"CM-HE-2001", "CM-MINESUP-FORM", "INT-ADDIS-2014", "CM-VERIF-PLATFORM-2024", "UAE-MOHESR-RECOG"}
    ai = {"AU-AI-2024", "NIST-AI-RMF-1.0", "EU-AI-ACT-2024", "CM-SNIA-2025", "OECD-AI-2019"}
    assert len(edu & have) >= 3 and len(ai & have) >= 3


def test_dimensions_json_vendored():
    p = loader.kb_dir() / "framework" / "dimensions.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert len(d["dimensions"]) == 13


def test_gap_register_anchors_resolve():
    import yaml
    seed = yaml.safe_load(open(loader.kb_dir() / "gaps" / "seed_gaps.yaml", encoding="utf-8"))["gaps"]
    ids = [g["id"] for g in seed]
    assert len(ids) == len(set(ids)) and {"P1", "P2", "P3", "P4", "P5", "P6", "S1", "S2", "S3", "S4"} <= set(ids)
    for g in seed:
        for ref in g.get("law_cited") or []:
            assert loader.resolve_ref(ref["anchor"]), f"{g['id']}: {ref['anchor']} unresolved"
        assert g.get("proposal") and g.get("evidence") is not None
