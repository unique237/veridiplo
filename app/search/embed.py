"""Embeddings for the evidence search engine (local, offline-capable).

Primary model: ``intfloat/multilingual-e5-small`` (384-d) with the mandatory ``query:`` / ``passage:`` prefixes.
Fallback (model cannot be loaded or downloaded): a deterministic hashing vectoriser over word and character
n-grams, L2-normalised, 384-d, so the pgvector column and cosine search keep working without any network.

Public API (used by the ingestion agent and the hybrid search):
    embed_passages(list[str]) -> list[list[float]]
    embed_query(str) -> list[float]
    embed_backend() -> "e5" | "hashing"

The model is a lazy singleton (nothing heavy is imported at module import time) and a small LRU cache
avoids recomputing query embeddings.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import unicodedata
from functools import lru_cache
from typing import Sequence

EMBED_DIM = 384
log = logging.getLogger(__name__)

_lock = threading.Lock()
_model = None  # SentenceTransformer | False (load failed) | None (not tried yet)
_BATCH = 64


def _model_name() -> str:
    return os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")


def _load_model():
    """Load the sentence-transformers model once; remember a failure so we do not retry on every call."""
    global _model
    if _model is not None:
        return _model or None
    with _lock:
        if _model is not None:
            return _model or None
        if os.getenv("EMBED_BACKEND", "").lower() in ("hashing", "tfidf", "fallback"):
            _model = False
            return None
        _model = _try_load()
        return _model or None


def _try_load():
    """Cache first (offline), then a time-boxed download; False when neither works (hashing fallback)."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        log.warning("sentence-transformers unavailable (%s) - using deterministic hashing fallback", exc)
        return False

    def _check(m):
        dim = m.get_sentence_embedding_dimension()
        if dim != EMBED_DIM:
            log.warning("embedding model has dim %s != %s, using hashing fallback", dim, EMBED_DIM)
            return False
        log.info("embedding backend: %s", _model_name())
        return m

    try:  # 1. already in the HF cache volume: no network needed
        return _check(SentenceTransformer(_model_name(), device="cpu", local_files_only=True))
    except Exception:
        pass
    if os.getenv("EMBED_ALLOW_DOWNLOAD", "1") == "0":
        log.warning("embedding model not cached and downloads disabled - using hashing fallback")
        return False
    box: dict = {}

    def _dl():
        try:
            box["m"] = SentenceTransformer(_model_name(), device="cpu")
        except Exception as exc:
            box["err"] = exc

    t = threading.Thread(target=_dl, daemon=True)
    t.start()
    t.join(float(os.getenv("EMBED_LOAD_TIMEOUT", "90")))
    if "m" in box:
        return _check(box["m"])
    log.warning("embedding model unavailable (%s) - using deterministic hashing fallback",
                box.get("err") or "download timed out")
    return False


def embed_backend() -> str:
    return "e5" if _load_model() is not None else "hashing"


# ----------------------------------------------------------------------------- hashing fallback
_TOKEN = re.compile(r"[a-z0-9]+")


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _features(text: str) -> list[str]:
    toks = _TOKEN.findall(_fold(text))
    feats = [f"w:{t}" for t in toks]
    for t in toks:  # character trigrams make the vectoriser robust to OCR noise / spelling variants
        p = f"^{t}$"
        feats.extend(f"c:{p[i:i + 3]}" for i in range(max(1, len(p) - 2)))
    return feats


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for f in _features(text):
        h = hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest()
        n = int.from_bytes(h, "big")
        idx = n % EMBED_DIM
        sign = 1.0 if (n >> 40) & 1 else -1.0
        vec[idx] += sign * (0.5 if f.startswith("c:") else 1.0)
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


# ----------------------------------------------------------------------------- public API
def embed_passages(texts: Sequence[str]) -> list[list[float]]:
    """Embed documents (prefix ``passage: `` for e5). Batched; returns one 384-d list per text."""
    texts = list(texts)
    if not texts:
        return []
    model = _load_model()
    if model is None:
        return [_hash_embed(t) for t in texts]
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        batch = [f"passage: {t or ''}" for t in texts[i:i + _BATCH]]
        vecs = model.encode(batch, batch_size=_BATCH, normalize_embeddings=True, show_progress_bar=False)
        out.extend([float(x) for x in v] for v in vecs)
    return out


@lru_cache(maxsize=2048)
def _embed_query_cached(q: str) -> tuple[float, ...]:
    model = _load_model()
    if model is None:
        return tuple(_hash_embed(q))
    v = model.encode([f"query: {q}"], normalize_embeddings=True, show_progress_bar=False)[0]
    return tuple(float(x) for x in v)


def embed_query(text: str) -> list[float]:
    """Embed a search query (prefix ``query: `` for e5); cached."""
    return list(_embed_query_cached((text or "").strip()))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
