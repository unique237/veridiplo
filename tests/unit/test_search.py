"""Unit tests: embeddings (offline hashing backend), RRF fusion, tsquery building, retrieval helpers.

DB-backed tests are skipped automatically when no seeded Postgres is reachable.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("EMBED_BACKEND", "hashing")  # never download a model in unit tests

from app.agents.retrieval import name_sim, row_name, row_serial  # noqa: E402
from app.search import embed  # noqa: E402
from app.search.hybrid import build_tsquery, rrf_fuse  # noqa: E402


def test_embeddings_are_384d_unit_norm_and_deterministic():
    a = embed.embed_passages(["DAIROU Anne-Marie 13.45 Assez Bien"])[0]
    b = embed.embed_passages(["DAIROU Anne-Marie 13.45 Assez Bien"])[0]
    assert len(a) == 384 and a == b
    assert sum(x * x for x in a) == pytest.approx(1.0, abs=1e-6)


def test_embeddings_batch_and_empty():
    assert embed.embed_passages([]) == []
    out = embed.embed_passages([f"row {i}" for i in range(130)])
    assert len(out) == 130 and all(len(v) == 384 for v in out)


def test_similar_text_is_closer_than_unrelated():
    q = embed.embed_query("Anne-Marie Dairou")
    near = embed.embed_passages(["DAIROU Anne-Marie 13.45 Assez Bien"])[0]
    far = embed.embed_passages(["TCHUMBAWOU Clementine 14.5 Good"])[0]
    assert embed.cosine(q, near) > embed.cosine(q, far)


def test_hashing_fallback_tolerates_ocr_noise_and_accents():
    q = embed.embed_query("Etoundi Mballa")
    noisy = embed.embed_passages(["ETOUNDI-MBALLA OWONO"])[0]
    other = embed.embed_passages(["KAMGA Eric"])[0]
    assert embed.cosine(q, noisy) > embed.cosine(q, other)


def test_query_cache_returns_equal_vectors():
    assert embed.embed_query("abc") == embed.embed_query("abc ")


def test_rrf_fusion_rewards_agreement_between_channels():
    fused, ranks = rrf_fuse({"fts": {1: 9.0, 2: 5.0, 3: 1.0}, "vector": {2: 0.9, 1: 0.8, 4: 0.7}})
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 63) and fused[4] == pytest.approx(1 / 63)
    assert ranks["fts"] == {1: 1, 2: 2, 3: 3} and ranks["vector"] == {2: 1, 1: 2, 4: 3}
    best = max(fused, key=fused.get)
    assert best in (1, 2)


def test_rrf_single_channel_and_empty():
    fused, _ = rrf_fuse({"fts": {7: 1.0}, "vector": {}})
    assert fused == {7: pytest.approx(1 / 61)}
    assert rrf_fuse({})[0] == {}


def test_tsquery_or_semantics_with_accent_variants():
    q = build_tsquery("Éric-Joël PENDA")
    toks = q.split(" | ")
    assert "éric" in toks and "eric" in toks and "penda" in toks
    assert build_tsquery("") == ""


def test_tsquery_is_injection_safe():
    q = build_tsquery("a'; DROP TABLE chunks; --")
    assert ";" not in q and "'" not in q and "-" not in q.replace(" | ", "")


def test_name_similarity_is_order_and_accent_insensitive():
    assert name_sim("DAIROU Anne-Marie", "Anne Marie DAIROU") == 100.0
    assert name_sim("Éric-Joël PENDA", "PENDA Eric Joel") == 100.0
    assert name_sim("Jean-Remi ETOUND-MBALL OWONO", "ÉTOUNDI-MBALLA OWONO Jean-Rémi Éloi") < 92
    assert name_sim("HAMAN Marie", "DAIROU Anne-Marie") < 80


def test_row_helpers_handle_registry_and_pv_shapes():
    assert row_name({"name_raw": "Éric PENDA"}) == "ERIC PENDA"
    assert row_name({"nom": " TSANGA ", "prenoms": "Marie"}) == "TSANGA MARIE"
    assert row_name({}, "KAMGA ERIC") == "KAMGA ERIC"
    assert row_serial({"numero_diplome": " ufn/2022/ldroit/00081 "}) == "UFN/2022/LDROIT/00081"
    assert row_serial({"serial": None}) is None


# ----------------------------------------------------------------------------- DB-backed (skipped when unseeded)
def _db():
    try:
        from sqlalchemy import text

        from app.core.db import SessionLocal
        s = SessionLocal()
        if not s.execute(text("select count(*) from chunks where kind in ('pv_row','registry_row')")).scalar():
            s.close()
            return None
        return s
    except Exception:
        return None


@pytest.fixture()
def db():
    s = _db()
    if s is None:
        pytest.skip("no seeded database")
    yield s
    s.close()


def test_search_returns_provenance_and_breakdown(db):
    import time

    from app.search.hybrid import search
    search(db, "warm up", limit=1)  # first call may load the embedding model
    t0 = time.perf_counter()
    hits = search(db, "DAIROU Anne-Marie", limit=5)
    assert time.perf_counter() - t0 < 1.0
    assert hits
    h = hits[0]
    for k in ("chunk_id", "document_id", "snippet", "page", "bbox", "page_size", "score", "score_breakdown", "fields",
              "institution_id", "programme_id", "session_year", "source_file", "crop_url"):
        assert k in h
    assert set(("fts", "vector", "rrf")) <= set(h["score_breakdown"])
    assert h["crop_url"] == f"/search/crop/{h['chunk_id']}.png"


def test_search_filters_are_applied(db):
    from app.search.hybrid import search
    hits = search(db, "Marie", institution="UFN", year=2022, limit=20)
    assert all(h["institution_id"] == "UFN" and h["session_year"] == 2022 for h in hits)


def test_serial_regex_from_kg_pattern():
    import re

    from app.agents.retrieval import _regex_from_pattern
    # a regex stored as-is (quantifiers like \d{4} must not be mistaken for tokens)
    assert _regex_from_pattern(r"^UFN/\d{4}/[A-Z]{3,7}/\d{5}$") == r"^UFN/\d{4}/[A-Z]{3,7}/\d{5}$"
    rx = _regex_from_pattern("UFN/{YYYY}/{PROG}/{NNNNN}")
    assert re.fullmatch(rx, "UFN/2019/LINFO/00123") and not re.fullmatch(rx, "UFN/2019/LINFO/123")
    assert _regex_from_pattern(None) is None


def test_token_alignment_similarity_is_robust_to_order_changing_slips():
    from app.agents.matching import token_align_sim
    assert name_sim("ENAMA MBIDA Anne-Marie", "ZNAMA MBIDA Anne-Marie") < 80
    assert token_align_sim("ENAMA MBIDA Anne-Marie", "ZNAMA MBIDA Anne-Marie") > 90
    assert token_align_sim("HAMAN Marie", "MAIGARI Rose-Marie") < 80
    assert token_align_sim("", "X") == 0.0
