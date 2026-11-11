"""OCRAgent (Y.3172 node PP): rasterise -> deskew -> OCR (fra+eng) -> words with bbox -> PV table rows -> chunks.

Pipeline
--------
1. ``rasterise_page``  PDF page (PyMuPDF) or image -> grayscale numpy array at ``OCR_DPI``.
2. ``preprocess``      deskew (projection-profile skew estimate; scans are rotated +-2 deg, which would otherwise
                       drift rows by more than one row pitch across the page width) and light denoising.
   The deskewed raster is THE page image: OCR coordinates, ``Chunk.bbox``/``page_size`` and ``render_crop`` all
   live in its pixel space (cached under DATA_DIR/cache/pages/).
3. ``ocr_image``       Tesseract (pytesseract, ``-l fra+eng``) with a graceful fallback to RapidOCR when the
                       tesseract binary is missing (``OCR_ENGINE=auto|tesseract|rapidocr``).
4. ``extract_pv_rows`` y-clustering of word boxes into lines, column detection from the header line, one row per
                       candidate; repairs for merged cells; dirty values normalised (``app.core.normalize``).
5. ``build_chunks``    page-level text chunks + row-level chunks (``pv_row``) with bbox / page_size / ocr_conf.

Results are cached as JSON under DATA_DIR/cache/ocr/<sha256>.json so that re-seeding is fast.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import shutil
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np

from app.core import refdata
from app.core.hashing import sha256_file
from app.core.names import normalize_name
from app.core.normalize import (mention_for_average, normalize_decision, normalize_mention, parse_average,
                                parse_date, parse_session_year)

log = logging.getLogger("veridiplo.ocr")

OCR_DPI = 200
OCR_VERSION = "6"  # bump to invalidate caches
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

# ------------------------------------------------------------------------------------------------ paths


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[2] / "data")


def _cache_dir(sub: str) -> Path:
    p = data_dir() / "cache" / sub
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:  # read-only data dir: fall back to a temp location
        p = Path("/tmp/veridiplo_cache") / sub
        p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------------------------------------ raster


def page_count(path: str | Path) -> int:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(str(path)) as d:
            return d.page_count
    from PIL import Image

    with Image.open(path) as im:
        return getattr(im, "n_frames", 1)


def rasterise_page(path: str | Path, page: int = 1, dpi: int = OCR_DPI) -> np.ndarray:
    """Grayscale uint8 raster of page ``page`` (1-based)."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(str(path)) as d:
            pix = d[page - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    from PIL import Image

    with Image.open(path) as im:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(page - 1)
        return np.asarray(im.convert("L"), dtype=np.uint8).copy()


def estimate_skew(gray: np.ndarray, limit: float = 4.0) -> float:
    """Skew angle in degrees (counter-clockwise positive) to apply to the image to straighten text lines.

    Projection-profile search: the angle that maximises the variance of the horizontal ink profile.
    """
    import cv2

    h, w = gray.shape
    scale = 900.0 / max(w, 1)
    small = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else gray
    small = cv2.GaussianBlur(small, (3, 3), 0)
    thr = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
    # light-gray watermark pixels must not count as ink: stay clearly below the Otsu level
    ink = (small < min(thr, 150)).astype(np.float32)
    if ink.sum() < 50:
        return 0.0
    hh, ww = ink.shape
    centre = (ww / 2, hh / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(centre, angle, 1.0)
        r = cv2.warpAffine(ink, m, (ww, hh), flags=cv2.INTER_LINEAR, borderValue=0)
        prof = r.sum(axis=1)
        return float(np.sum(np.diff(prof) ** 2))

    coarse = np.arange(-limit, limit + 1e-6, 0.4)
    best = max(coarse, key=score)
    fine = np.arange(best - 0.4, best + 0.4 + 1e-6, 0.05)
    best = max(fine, key=score)
    return float(best)


def preprocess(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Deskew (+ mild denoise). Returns (image, applied_angle_deg)."""
    import cv2

    angle = estimate_skew(gray)
    if abs(angle) >= 0.1:
        h, w = gray.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        gray = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                              borderValue=255)
    return gray, angle


# column width template of the PV layout (points); used only to complete partially detected vertical rulings
_COL_TEMPLATE = [26, 72, 208, 52, 86, 71]


def _complete_columns(vx: list[float], x0: float, x1: float) -> list[float]:
    """Vertical rulings missing (broken lines on poor scans): rebuild all column edges from the layout template when
    every detected ruling agrees with a template edge; otherwise return the detected ones unchanged."""
    if len(vx) < 2 or x1 - x0 < 200:
        return vx
    tot = float(sum(_COL_TEMPLATE))
    edges, acc = [x0], 0.0
    for wcol in _COL_TEMPLATE:
        acc += wcol
        edges.append(x0 + (x1 - x0) * acc / tot)
    tol = 0.015 * (x1 - x0)
    if all(min(abs(v - e) for e in edges) <= tol for v in vx):
        return [float(e) for e in edges]
    return vx


def remove_table_lines(gray: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Erase ruled table lines (they derail Tesseract's layout analysis) and flatten light cell fills.

    Returns (cleaned image, grid) where ``grid = {"h": [y...], "v": [x...], "x0", "x1"}`` lists the detected ruling
    positions in pixel coordinates (used to derive exact row bounding boxes).
    """
    import cv2

    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[0]
    ink = (blur < min(thr, 170)).astype(np.uint8) * 255
    hl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 12), 1)))
    vl = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40)))
    mask = cv2.dilate(cv2.bitwise_or(hl, vl), np.ones((5, 5), np.uint8))
    out = gray.copy()
    out[mask > 0] = 255
    out = np.where(out > 185, 255, out).astype(np.uint8)

    def centres(profile: np.ndarray, min_len: float) -> list[float]:
        idx = np.where(profile >= min_len)[0]
        groups: list[list[int]] = []
        for i in idx:
            if groups and i - groups[-1][-1] <= 3:
                groups[-1].append(int(i))
            else:
                groups.append([int(i)])
        return [float(np.mean(g)) for g in groups]

    hy = centres((hl > 0).sum(axis=1), w * 0.25)
    vx: list[float] = []
    if len(hy) >= 2:  # verticals count only when they span (most of) the table height
        span = vl[int(hy[0]):int(hy[-1]) + 1]
        vx = centres((span > 0).sum(axis=0), 0.6 * (hy[-1] - hy[0]))
    xs = np.where((hl > 0).sum(axis=0) > 0)[0]
    x0 = float(xs.min()) if len(xs) else None
    x1 = float(xs.max()) if len(xs) else None
    if x0 is not None and len(vx) != len(GRID_COLS) + 1:
        vx = _complete_columns(vx, x0, x1)
    grid = {"h": hy, "v": vx, "x0": x0, "x1": x1}
    return out, grid


def page_image_path(sha: str, page: int, dpi: int = OCR_DPI) -> Path:
    return _cache_dir("pages") / f"{sha}_p{page}_{dpi}.png"


def get_page_image(path: str | Path, sha: str | None, page: int = 1) -> np.ndarray:
    """Deskewed page raster (the OCR coordinate space), cached as PNG."""
    from PIL import Image

    sha = sha or sha256_file(path)
    cp = page_image_path(sha, page)
    if cp.is_file():
        return np.asarray(Image.open(cp).convert("L"), dtype=np.uint8)
    img, _ = preprocess(rasterise_page(path, page))
    try:
        Image.fromarray(img).save(cp, optimize=False, compress_level=3)
    except OSError:  # pragma: no cover
        pass
    return img


# ------------------------------------------------------------------------------------------------ engines

_RAPID = None


def available_engines() -> list[str]:
    """Engines to run on every page, primary first. ``OCR_ENGINE=auto`` (default) uses every installed engine
    (Tesseract primary, RapidOCR as second opinion: the two are merged field by field on ruled PV tables);
    ``OCR_ENGINE=tesseract|rapidocr`` forces one; ``OCR_ENSEMBLE=0`` keeps only the primary."""
    pref = os.environ.get("OCR_ENGINE", "auto").lower()
    if pref in ("tesseract", "rapidocr"):
        return [pref]
    found = []
    if shutil.which("tesseract"):
        try:
            import pytesseract  # noqa: F401
            found.append("tesseract")
        except ImportError:
            pass
    try:
        import rapidocr_onnxruntime  # noqa: F401
        found.append("rapidocr")
    except ImportError:
        pass
    if not found:
        raise RuntimeError("No OCR engine available (need the tesseract binary + pytesseract, or rapidocr-onnxruntime)")
    return found if os.environ.get("OCR_ENSEMBLE", "1") != "0" else found[:1]


def available_engine() -> str:
    """Primary engine name (raises when none is installed)."""
    return available_engines()[0]


def engine_signature() -> str:
    try:
        return "+".join(available_engines())
    except RuntimeError:
        return "none"


def _ocr_tesseract(img: np.ndarray) -> list[dict[str, Any]]:
    import pytesseract

    cfg = os.environ.get("TESSERACT_CONFIG", "--oem 1 --psm 6")
    d = pytesseract.image_to_data(img, lang=os.environ.get("OCR_LANG", "fra+eng"), config=cfg,
                                  output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not t or conf < 0:
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        words.append({"t": t, "b": [float(x), float(y), float(x + w), float(y + h)], "c": round(conf / 100.0, 3)})
    return words


def _split_merged(text: str) -> list[str]:
    """RapidOCR (legacy model) drops spaces inside a detected segment: re-split at lower->Upper boundaries and
    before a Capitalised word that follows capitals ("TEFOUETJosianeJean" -> TEFOUET Josiane Jean)."""
    out: list[str] = []
    for tok in text.split():
        tok = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", " ", tok)
        tok = re.sub(r"(?<=[A-ZÀ-Þ]{2})(?=[A-ZÀ-Þ][a-zà-ÿ])", " ", tok)
        out += tok.split()
    return out


def _ocr_rapid(img: np.ndarray) -> list[dict[str, Any]]:
    global _RAPID
    if _RAPID is None:
        from rapidocr_onnxruntime import RapidOCR

        _RAPID = RapidOCR()
    res, _ = _RAPID(img)
    words: list[dict[str, Any]] = []
    for box, text, conf in res or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        toks = _split_merged(str(text))
        if not toks:
            continue
        total = sum(len(t) for t in toks) + (len(toks) - 1)
        cur = 0
        for t in toks:  # proportional split of a detected segment into words
            a = x0 + (x1 - x0) * cur / total
            b = x0 + (x1 - x0) * (cur + len(t)) / total
            words.append({"t": t, "b": [float(a), float(y0), float(b), float(y1)], "c": round(float(conf), 3)})
            cur += len(t) + 1
    return words


def ocr_image(img: np.ndarray, engine: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Words ``{t, b:[x0,y0,x1,y1], c:0..1}`` in pixel coordinates of ``img`` and the engine name used."""
    engine = engine or available_engine()
    if engine == "tesseract":
        try:
            return _ocr_tesseract(img), "tesseract"
        except Exception as exc:  # binary missing / crash -> fallback chain
            log.warning("tesseract failed (%s), falling back to rapidocr", exc)
    return _ocr_rapid(img), "rapidocr"


def pdf_text_words(path: str | Path, page: int, dpi: int = OCR_DPI) -> list[dict[str, Any]]:
    """Words from the embedded text layer (digital PDFs), scaled to ``dpi`` pixel coordinates.

    Rotated text (the diagonal SPECIMEN watermark) is dropped: only words lying inside an upright text line and not
    taller than that line are kept.
    """
    import pymupdf

    k = dpi / 72.0
    out = []
    with pymupdf.open(str(path)) as d:
        pg = d[page - 1]
        uprights = []
        for blk in pg.get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                if abs(ln["dir"][1]) < 0.02 and ln["bbox"][3] - ln["bbox"][1] > 0:
                    uprights.append(ln["bbox"])
        for x0, y0, x1, y1, t, *_ in pg.get_text("words"):
            if not t.strip():
                continue
            h = y1 - y0
            if not any(u[0] - 1 <= x0 and x1 <= u[2] + 1 and u[1] - 1 <= y0 and y1 <= u[3] + 1 and h <= 1.3 * (u[3] - u[1])
                       for u in uprights):
                continue
            out.append({"t": t, "b": [x0 * k, y0 * k, x1 * k, y1 * k], "c": 1.0})
    return out


# ------------------------------------------------------------------------------------------------ page OCR


def ocr_page_task(args: tuple[str, int, str, bool]) -> dict[str, Any]:
    """Top-level (picklable) worker: OCR one page. args = (path, page, sha, use_text_layer)."""
    path, page, sha, use_text = args
    img = get_page_image(path, sha, page)
    h, w = img.shape
    words: list[dict[str, Any]] = []
    engine = "text-layer"
    alt: dict[str, Any] | None = None
    if use_text and Path(path).suffix.lower() == ".pdf":
        words = pdf_text_words(path, page)  # digital PDF: the text layer is exact (and not skewed)
    clean, grid = remove_table_lines(img)
    if not words:
        engines = available_engines()
        words, engine = ocr_image(clean, engines[0])
        if len(engines) > 1:  # second opinion (merged per field on ruled tables)
            w2, e2 = ocr_image(clean, engines[1])
            if e2 != engine and w2:
                alt = {"engine": e2, "words": w2}
    confs = [x["c"] for x in words]
    out = {"page": page, "size": [w, h], "words": words, "engine": engine, "grid": grid,
           "conf": round(float(np.mean(confs)), 4) if confs else 0.0}
    if alt:
        out["alt"] = alt
    return out


def _ocr_cache_path(sha: str) -> Path:
    return _cache_dir("ocr") / f"{sha}.json"


def _load_cached(sha: str) -> dict[str, Any] | None:
    p = _ocr_cache_path(sha)
    if p.is_file():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            text_only = all(p.get("engine") == "text-layer" for p in obj.get("pages", []))
            if obj.get("version") == OCR_VERSION and obj.get("dpi") == OCR_DPI and \
                    (text_only or obj.get("engines") == engine_signature()):
                return obj
        except Exception:
            return None
    return None


def _has_text_layer(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    import pymupdf

    with pymupdf.open(str(path)) as d:
        return any(len(p.get_text("words")) > 15 for p in d)


def ocr_document(path: str | Path, sha: str | None = None, workers: int = 1, use_cache: bool = True,
                 pool: ProcessPoolExecutor | None = None) -> dict[str, Any]:
    """OCR every page. Returns ``{version, dpi, sha, pages:[{page,size,words,engine,conf}], conf}`` (cached)."""
    path = Path(path)
    sha = sha or sha256_file(path)
    if use_cache:
        c = _load_cached(sha)
        if c:
            return c
    n = page_count(path)
    use_text = _has_text_layer(path)
    tasks = [(str(path), p, sha, use_text) for p in range(1, n + 1)]
    if pool is not None:
        pages = list(pool.map(ocr_page_task, tasks))
    elif workers > 1 and n > 1:
        with ProcessPoolExecutor(workers, mp_context=_mp_ctx()) as ex:
            pages = list(ex.map(ocr_page_task, tasks))
    else:
        pages = [ocr_page_task(t) for t in tasks]
    confs = [p["conf"] for p in pages if p["words"]]
    doc = {"version": OCR_VERSION, "dpi": OCR_DPI, "engines": engine_signature(), "sha": sha, "pages": pages,
           "conf": round(float(np.mean(confs)), 4) if confs else 0.0}
    if use_cache:
        try:
            _ocr_cache_path(sha).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        except OSError:  # pragma: no cover
            pass
    return doc


def _mp_ctx():
    import multiprocessing as mp

    # one thread per worker process: onnxruntime/OpenMP would otherwise oversubscribe the cores
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")

    return mp.get_context("spawn")


def ocr_many(paths: Iterable[str | Path], workers: int = 4, progress: bool = False) -> dict[str, dict[str, Any]]:
    """OCR many documents in parallel (page granularity, cache aware). Returns {path: ocr_doc}."""
    _mp_ctx()  # sets the per-worker thread limits in the environment inherited by the spawned workers
    paths = [Path(p) for p in paths]
    shas = {str(p): sha256_file(p) for p in paths}
    out: dict[str, dict[str, Any]] = {}
    todo: list[tuple[str, int, str, bool]] = []
    meta: dict[str, int] = {}
    for p in paths:
        c = _load_cached(shas[str(p)])
        if c:
            out[str(p)] = c
            continue
        n = page_count(p)
        meta[str(p)] = n
        ut = _has_text_layer(p)
        todo += [(str(p), i, shas[str(p)], ut) for i in range(1, n + 1)]
    if todo:
        log.info("OCR: %d pages in %d documents (%d workers, engine=%s)", len(todo), len(meta), workers,
                 engine_signature())
        if workers > 1:
            with ProcessPoolExecutor(workers, mp_context=_mp_ctx()) as ex:
                results = []
                for i, r in enumerate(ex.map(ocr_page_task, todo, chunksize=1), 1):
                    results.append(r)
                    if progress and i % 20 == 0:
                        print(f"  OCR {i}/{len(todo)} pages", flush=True)
        else:
            results = [ocr_page_task(t) for t in todo]
        by_path: dict[str, list[dict]] = {}
        for t, r in zip(todo, results):
            by_path.setdefault(t[0], []).append(r)
        for p, pages in by_path.items():
            pages.sort(key=lambda r: r["page"])
            confs = [x["conf"] for x in pages if x["words"]]
            doc = {"version": OCR_VERSION, "dpi": OCR_DPI, "engines": engine_signature(), "sha": shas[p], "pages": pages,
                   "conf": round(float(np.mean(confs)), 4) if confs else 0.0}
            try:
                _ocr_cache_path(shas[p]).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            except OSError:  # pragma: no cover
                pass
            out[p] = doc
    return out


# ------------------------------------------------------------------------------------------------ layout


def cluster_lines(words: list[dict[str, Any]], tol: float | None = None) -> list[list[dict[str, Any]]]:
    """Group word boxes into text lines by vertical-centre clustering; each line sorted left to right."""
    if not words:
        return []
    heights = [w["b"][3] - w["b"][1] for w in words]
    med_h = statistics.median(heights) if heights else 20.0
    tol = tol if tol is not None else max(6.0, 0.55 * med_h)
    ws = sorted(words, key=lambda w: (w["b"][1] + w["b"][3]) / 2)
    lines: list[list[dict[str, Any]]] = []
    centres: list[float] = []
    for w in ws:
        cy = (w["b"][1] + w["b"][3]) / 2
        if lines and abs(cy - centres[-1]) <= tol:
            lines[-1].append(w)
            centres[-1] = sum((x["b"][1] + x["b"][3]) / 2 for x in lines[-1]) / len(lines[-1])
        else:
            lines.append([w])
            centres.append(cy)
    for ln in lines:
        ln.sort(key=lambda w: w["b"][0])
    return lines


def line_text(line: list[dict[str, Any]]) -> str:
    return " ".join(w["t"] for w in line)


def line_bbox(line: list[dict[str, Any]]) -> list[float]:
    return [min(w["b"][0] for w in line), min(w["b"][1] for w in line),
            max(w["b"][2] for w in line), max(w["b"][3] for w in line)]


def page_text(words: list[dict[str, Any]]) -> str:
    return "\n".join(line_text(l) for l in cluster_lines(words))


# header keywords (FR + EN) -> column name
_HEAD_KEYS = {
    "matricule": "matricule", "matric": "matricule", "reg": "matricule", "regno": "matricule",
    "moyenne": "average", "average": "average", "avg": "average", "moy": "average",
    "mention": "mention", "grade": "mention",
    "decision": "decision", "décision": "decision", "resultat": "decision",
}
_MATRICULE_RE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{3,15}$")
_AVG_RE = re.compile(r"^\d{1,2}[.,]\d{1,2}$")
_MENTION_WORDS = {"passable", "assez", "bien", "tres", "très", "pass", "fairly", "good", "very", "excellent"}


def _fold(s: str) -> str:
    from unidecode import unidecode

    return re.sub(r"[^a-z]", "", unidecode(s).lower())


def find_header(lines: list[list[dict[str, Any]]]) -> tuple[int, dict[str, float]] | None:
    """Locate the table header line and the left x of each column: {n, matricule, name, average, mention, decision}."""
    for i, ln in enumerate(lines):
        cols: dict[str, float] = {}
        for w in ln:
            key = _HEAD_KEYS.get(_fold(w["t"]))
            if key and key not in cols:
                cols[key] = w["b"][0]
            elif _fold(w["t"]) == "nom" and "name" not in cols:
                cols["name"] = w["b"][0]
            elif _fold(w["t"]).startswith("n") and w["t"].strip().startswith(("N°", "N0", "No", "N°")) and "n" not in cols:
                cols["n"] = w["b"][0]
        if len(cols) >= 3 and ("matricule" in cols or "name" in cols) and ("average" in cols or "mention" in cols):
            return i, cols
    return None


def default_columns(width: float) -> dict[str, float]:
    """Fallback column x positions (fractions of page width, measured on the generated PV layout)."""
    return {"n": 0.074 * width, "matricule": 0.118 * width, "name": 0.238 * width, "average": 0.588 * width,
            "mention": 0.675 * width, "decision": 0.820 * width}


def _col_bounds(cols: dict[str, float], width: float) -> list[tuple[str, float, float]]:
    order = ["n", "matricule", "name", "average", "mention", "decision"]
    xs = []
    for k in order:
        if k in cols:
            xs.append((k, cols[k]))
    out = []
    for j, (k, x) in enumerate(xs):
        lo = (-1.0 if j == 0 else x - 0.012 * width)
        hi = (width * 2 if j == len(xs) - 1 else xs[j + 1][1] - 0.012 * width)
        out.append((k, lo, hi))
    return out


def _clean_matricule(t: str) -> str:
    return re.sub(r"[^A-Z0-9/-]", "", t.upper()).strip("-/")


def _is_row_start(tok: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}[.)]?", tok.strip()))


def _looks_matricule(tok: str, exact: bool = False) -> bool:
    """Registration-number-like token (letters+digits). ``exact``: token sits in the ruled matricule cell, so a
    digits-only reading (OCR turned a letter into a digit) is accepted too and repaired later."""
    t = _clean_matricule(tok)
    if not _MATRICULE_RE.match(t) or _AVG_RE.match(tok) or not any(c.isdigit() for c in t):
        return False
    return exact and len(t) >= 5 or any(c.isalpha() for c in t)


_D2L = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
_L2D = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "A": "4", "S": "5", "G": "6", "T": "7", "B": "8"}


def _shape(m: str) -> str:
    return "".join("D" if c.isdigit() else "L" if c.isalpha() else c for c in m)


def repair_matricules(rows: list[dict[str, Any]]) -> int:
    """Fix OCR confusions in registration numbers using the document itself.

    1. digit/letter confusions (B<->8, O<->0, S<->5 ...) against the dominant shape (e.g. ``DDLDDDD`` for 21B2503);
    2. template characters: positions where >= 70 % of the rows agree on one character (cohort year, institution /
       programme code such as ``ISD``) are snapped to that character when the row differs in at most 2 positions.
    Returns the number of repaired rows.
    """
    from collections import Counter

    # ids are alphanumeric: digits-only readings are the OCR confusions to repair, never the reference shape
    shapes = Counter(_shape(r["matricule"]) for r in rows if r.get("matricule") and re.search(r"[A-Z]", r["matricule"]))
    if not shapes:
        return 0
    shape, n = shapes.most_common(1)[0]
    if n < 2 or n < 0.5 * sum(shapes.values()):
        return 0
    fixed = set()
    for i, r in enumerate(rows):
        m = r.get("matricule") or ""
        if len(m) != len(shape) or _shape(m) == shape:
            continue
        out = []
        for ch, want in zip(m, shape):
            if want == "L" and ch.isdigit():
                ch = _D2L.get(ch, ch)
            elif want == "D" and ch.isalpha():
                ch = _L2D.get(ch, ch)
            out.append(ch)
        cand = "".join(out)
        if _shape(cand) == shape:
            r["matricule_raw"] = m
            r["matricule"] = cand
            fixed.add(i)
    same = [r for r in rows if len(r.get("matricule") or "") == len(shape)]
    if len(same) >= 3:
        consensus: dict[int, str] = {}
        for pos in range(len(shape)):
            ch, cnt = Counter(r["matricule"][pos] for r in same).most_common(1)[0]
            if cnt >= 0.7 * len(same):
                consensus[pos] = ch
        for i, r in enumerate(rows):
            m = r.get("matricule") or ""
            if len(m) != len(shape):
                continue
            diff = [p for p, c in consensus.items() if m[p] != c]
            if diff and len(diff) <= 2 and len(consensus) >= 2:
                chars = list(m)
                for p in diff:
                    chars[p] = consensus[p]
                r.setdefault("matricule_raw", m)
                r["matricule"] = "".join(chars)
                fixed.add(i)
    return len(fixed)


def parse_row_line(line: list[dict[str, Any]], bounds: list[tuple[str, float, float]], exact: bool = False
                   ) -> dict[str, Any] | None:
    """One text line -> row dict, or None when it is not a data row. ``exact``: bounds are ruled cell edges."""
    cells: dict[str, list[dict[str, Any]]] = {k: [] for k, _, _ in bounds}
    for w in line:
        cx = (w["b"][0] + w["b"][2]) / 2
        left = w["b"][0]
        for k, lo, hi in bounds:
            ref = cx if exact or k == "n" else left
            if lo <= ref < hi:
                cells[k].append(w)
                break
    for k in cells:  # OCR residue of ruling / stamps: tokens without any letter or digit
        cells[k] = [w for w in cells[k] if re.search(r"[A-Za-zÀ-ÿ0-9]", w["t"])]
    if exact:  # name cell: stop at a large horizontal gap (stray marks far to the right of the name)
        kept: list[dict[str, Any]] = []
        for w in sorted(cells.get("name", []), key=lambda w: w["b"][0]):
            if kept and w["b"][0] - kept[-1]["b"][2] > 3.2 * (w["b"][3] - w["b"][1]):
                break
            kept.append(w)
        cells["name"] = [w for w in kept if re.search(r"[A-Za-zÀ-ÿ]", w["t"])]
    toks = {k: [w["t"] for w in v] for k, v in cells.items()}

    # --- repairs for merged / shifted cells (rapidocr segments, jitter) -------------------------
    flat = [w["t"] for w in line]
    n_txt = " ".join(toks.get("n", []))
    mat_txt = " ".join(toks.get("matricule", []))
    # N° and matricule glued into one token or matricule landed in name col
    if not toks.get("matricule") or not any(_looks_matricule(t, exact) for t in toks["matricule"]):
        for k in ("name", "n"):
            for t in list(toks.get(k, [])):
                if _looks_matricule(t) and not t.isalpha():
                    toks["matricule"] = [t]
                    toks[k].remove(t)
                    break
    if exact:  # the decimal point is often lost on poor scans: "948" -> "9.48" (PV averages always have 2 decimals)
        toks["average"] = [f"{t[:-2]}.{t[-2:]}" if re.fullmatch(r"\d{3,4}", t) else t for t in toks.get("average", [])]
    avg_tok = next((t for t in toks.get("average", []) if _AVG_RE.match(t.strip())), None)
    if avg_tok is None:
        for k in ("name", "mention"):
            for t in list(toks.get(k, [])):
                if _AVG_RE.match(t.strip()):
                    avg_tok = t
                    toks[k].remove(t)
                    break
            if avg_tok:
                break
    mention_txt = " ".join(toks.get("mention", []))
    if not mention_txt:
        # mention words that drifted into the average/name column
        mw = [t for t in toks.get("name", []) if _fold(t) in _MENTION_WORDS]
        if mw and normalize_mention(" ".join(mw)):
            mention_txt = " ".join(mw)
            toks["name"] = [t for t in toks["name"] if t not in mw]
    dec_txt = " ".join(toks.get("decision", []))

    # row number
    n_tok = next((t for t in toks.get("n", []) if _is_row_start(t)), None)
    mat = next((t for t in toks.get("matricule", []) if _looks_matricule(t, exact)), None)
    name_tokens = toks.get("name", [])
    if not mat or not name_tokens:
        return None
    if n_tok is None and not dec_txt and not avg_tok:
        return None
    name_raw = " ".join(name_tokens).strip()
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", name_raw):
        return None
    conf_ws = [w for w in line]
    return {
        "row_n": int(re.sub(r"\D", "", n_tok)) if n_tok else None,
        "matricule": _clean_matricule(mat),
        "name_raw": name_raw,
        "name_norm": normalize_name(name_raw),
        "average": parse_average(avg_tok) if avg_tok else None,
        "mention": normalize_mention(mention_txt) if mention_txt else None,
        "mention_raw": mention_txt or None,
        "decision": normalize_decision(dec_txt) if dec_txt else None,
        "decision_raw": dec_txt or None,
        "ocr_conf": round(float(np.mean([w["c"] for w in conf_ws])), 4),
        "_cells_conf": {k: (float(np.mean([w["c"] for w in v])) if v else 0.0) for k, v in cells.items()},
        "_line": line,
    }


GRID_COLS = ["n", "matricule", "name", "average", "mention", "decision"]


def grid_column_bounds(grid: dict[str, Any] | None) -> list[tuple[str, float, float]] | None:
    """Column cells from the table ruling when exactly six columns were detected."""
    if not grid:
        return None
    v = sorted(grid.get("v") or [])
    if len(v) != len(GRID_COLS) + 1:
        return None
    return [(k, v[i] + 2, v[i + 1] - 2) for i, k in enumerate(GRID_COLS)]


def _ruled_row_lines(page: dict[str, Any], words: list[dict[str, Any]] | None = None
                     ) -> list[list[dict[str, Any]]] | None:
    """Words grouped per ruled row band (between consecutive horizontal rulings), or None without a usable grid."""
    grid = page.get("grid") or {}
    hy = sorted(grid.get("h") or [])
    if len(hy) < 3 or grid_column_bounds(grid) is None:
        return None
    bands = []
    for y0, y1 in zip(hy, hy[1:]):
        if y1 - y0 < 15 or y1 - y0 > 400:
            continue
        ln = [w for w in (page["words"] if words is None else words) if y0 <= (w["b"][1] + w["b"][3]) / 2 < y1]
        ln.sort(key=lambda w: w["b"][0])
        bands.append(ln)
    return bands


def _letters(s: str | None) -> str:
    from unidecode import unidecode

    return re.sub(r"[^A-Z]", "", unidecode(s or "").upper())


def _band_of(page_words_rows: list[dict[str, Any] | None], i: int) -> dict[str, Any] | None:
    return page_words_rows[i] if i < len(page_words_rows) else None


def merge_rows(a: dict[str, Any] | None, b: dict[str, Any] | None, shape: str | None = None) -> dict[str, Any] | None:
    """Field-wise merge of the same table row read by two engines (a: primary/Tesseract, b: RapidOCR).

    Policy (measured on the synthetic corpus): digits (average) and the enumerated fields come from the engine whose
    value is valid / consistent; the name comes from ``a`` (spaces + accents) unless it looks noisy; the matricule
    follows the document's dominant shape. The mention-vs-average band is used only as a tie-break between the two
    readings, never to overwrite a reading (a PV row that contradicts itself must stay visible to C4).
    """
    if a is None or b is None:
        return a or b
    out = dict(a)
    # --- name
    ka, kb = _letters(a["name_raw"]), _letters(b["name_raw"])
    noisy = bool(re.search(r"[^A-Za-zÀ-ÿ' .-]", a["name_raw"])) or _conf_of(a, "name") < 0.5
    if ka != kb and (noisy or not ka):
        out.update(name_raw=b["name_raw"], name_norm=b["name_norm"])
    # --- matricule: RapidOCR reads digits/letters of ids far better than Tesseract on noisy scans
    if a["matricule"] != b["matricule"]:
        b_ok = len(b["matricule"]) >= 5 and not (shape and _shape(a["matricule"]) == shape != _shape(b["matricule"]))
        out["matricule"] = b["matricule"] if b_ok else a["matricule"]
    # --- average
    aa, ab = a.get("average"), b.get("average")
    out["average"] = ab if ab is not None else aa
    avg = out["average"]
    # --- mention / decision (enumerations): prefer the reading consistent with the average band
    exp_m = None if avg is None else mention_for_average(avg)
    ma, mb = a.get("mention"), b.get("mention")
    if ma != mb:
        pick = mb if (mb == exp_m or ma is None and avg is not None and avg >= 10) else ma if ma == exp_m else mb
        out.update(mention=pick, mention_raw=(b if pick == mb else a).get("mention_raw"))
    da, db = a.get("decision"), b.get("decision")
    if da != db:
        exp_d = None if avg is None else ("Admis" if avg >= 10 else "Ajourné")
        pick = da if (da == exp_d and db != exp_d) else db if (db == exp_d and da != exp_d) else (db or da)
        out.update(decision=pick, decision_raw=(b if pick == db else a).get("decision_raw"))
    out["ocr_conf"] = round(max(a["ocr_conf"], b["ocr_conf"]) if not noisy else b["ocr_conf"], 4)
    out["engines"] = "merged"
    return out


def _conf_of(row: dict[str, Any], key: str) -> float:
    return 0.0 if not row.get("_cells_conf") else float(row["_cells_conf"].get(key, 0.0))


def extract_pv_rows(page: dict[str, Any], prev_cols: dict[str, float] | None = None
                    ) -> tuple[list[dict[str, Any]], dict[str, float] | None]:
    """Rows of one OCR'd page: each ``{row_n, matricule, name_raw, name_norm, average, mention, decision,
    ocr_conf, bbox}`` with bbox in page pixel coordinates (full table width, one row pitch high).

    Returns (rows, header columns found (for the next page)).
    """
    w, h = page["size"]
    lines = cluster_lines(page["words"])
    hdr = find_header(lines)
    cols = hdr[1] if hdr else (prev_cols or default_columns(w))
    if "n" not in cols:
        cols = {**cols, "n": min(cols.values()) - 0.045 * w}
    rows = []
    bands = _ruled_row_lines(page)
    if bands is not None:  # ruled table: one row per cell band, columns are the ruled cells
        gb = grid_column_bounds(page["grid"])
        prim = [(ln and parse_row_line(ln, gb, exact=True)) or None for ln in bands]
        alt = None
        if page.get("alt"):  # second engine: same bands, merged field by field
            alt_bands = _ruled_row_lines(page, page["alt"]["words"]) or []
            alt = [(ln and parse_row_line(ln, gb, exact=True)) or None for ln in alt_bands]
        if alt is not None:
            from collections import Counter

            shapes = Counter(_shape(r["matricule"]) for r in prim + alt if r and re.search(r"[A-Z]", r["matricule"]))
            top = shapes.most_common(1)[0] if shapes else None
            shape = top[0] if top and top[1] >= 0.6 * sum(shapes.values()) else None  # None: no clear dominant shape
            merged = [merge_rows(_band_of(prim, i), _band_of(alt, i), shape) for i in range(max(len(prim), len(alt)))]
        else:
            merged = prim
        rows = [r for r in merged if r]
    if not rows:  # no ruling (or nothing parsed): y-clustering of word boxes + header-derived columns
        bounds = _col_bounds(cols, w)
        start = hdr[0] + 1 if hdr else 0
        for ln in lines[start:]:
            r = parse_row_line(ln, bounds)
            if r:
                rows.append(r)
    # geometry: exact cell bounds from the detected ruling when available, else the word-based estimate
    if rows:
        grid = page.get("grid") or {}
        hy = sorted(grid.get("h") or [])
        gx0, gx1 = grid.get("x0"), grid.get("x1")
        cys = [(line_bbox(r["_line"])[1] + line_bbox(r["_line"])[3]) / 2 for r in rows]
        heights = [line_bbox(r["_line"])[3] - line_bbox(r["_line"])[1] for r in rows]
        pitches = [b - a for a, b in zip(cys, cys[1:]) if 0 < b - a < 6 * statistics.median(heights)]
        pitch = statistics.median(pitches) if pitches else 2.3 * statistics.median(heights)
        xs0 = [line_bbox(r["_line"])[0] for r in rows]
        xs1 = [line_bbox(r["_line"])[2] for r in rows]
        tx0 = gx0 if gx0 is not None else max(0.0, min(xs0) - 0.012 * w)
        tx1 = gx1 if gx1 is not None else min(float(w), max(xs1) + 0.012 * w)
        for r, cy in zip(rows, cys):
            above = [y for y in hy if y <= cy]
            below = [y for y in hy if y > cy]
            if above and below and 0 < below[0] - above[-1] < 3 * pitch:
                y0, y1 = above[-1], below[0]
            else:
                y0, y1 = cy - pitch / 2, cy + pitch / 2
            r["bbox"] = [round(tx0, 1), round(y0, 1), round(tx1, 1), round(y1, 1)]
            r.pop("_line")
            r.pop("_cells_conf", None)
    return rows, (cols if hdr else prev_cols)


# ------------------------------------------------------------------------------------------------ PV header


def parse_pv_header(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Provenance fields from the PV preamble: institution, programme, session year, deliberation date, signatory."""
    out: dict[str, Any] = {}
    if not pages:
        return out
    lines = [line_text(l) for l in cluster_lines(pages[0]["words"])]
    head = "\n".join(lines[:25])
    out["is_pv"] = bool(re.search(r"PROC[EÈ]S[\s-]*VERBAL|MINUTES OF DELIBERATION", head, re.I))
    out["is_list"] = bool(re.search(r"LISTE DES ADMIS|LIST OF SUCCESSFUL", head, re.I))
    inst, sc = refdata.match_institution(" ".join(lines[:4]))
    if inst:
        out["institution_id"] = inst
    m = re.search(r"(?:Fili[eè]re|Programme)\s*/?\s*(?:Programme)?\s*:\s*(.+)", head, re.I)
    if m:
        prog_txt = m.group(1).strip()
        out["programme_text"] = prog_txt
        pid, _ = refdata.match_programme(prog_txt, inst)
        if pid:
            out["programme_id"] = pid
    m = re.search(r"Session\s*(?:de|of)?\s*([A-Za-zéèûôîÉ]+\s*)?((?:19|20)\d{2})", head, re.I)
    if m:
        out["session_year"] = int(m.group(2))
    m = re.search(r"(?:Date\s*de\s*d[eé]lib[eé]ration|Date\s*of\s*deliberation)\s*:\s*(.+)", head, re.I)
    if m:
        d = parse_date(m.group(1).strip())
        if d:
            out["deliberation_date"] = d
    m = re.search(r"Signataire\s*:\s*(.+?)(?:,\s*(.+))?$", head, re.I | re.M)
    if m:
        out["signatory_name"] = m.group(1).strip()
        if m.group(2):
            out["signatory_title"] = m.group(2).strip()
    m = re.search(r"Pr[eé]sident\s*du\s*jury\s*:\s*(.+)", head, re.I)
    if m:
        out["jury_president"] = m.group(1).strip()
    return out


# ------------------------------------------------------------------------------------------------ chunks


def format_row_text(row: dict[str, Any]) -> str:
    """'N° 12 | M123 | NAME | 13.42 | Assez Bien | Admis' (empty fields shown as '-')."""
    avg = f"{row['average']:.2f}" if row.get("average") is not None else "-"
    return " | ".join([
        f"N° {row['row_n']}" if row.get("row_n") is not None else "N° -",
        row.get("matricule") or "-",
        row.get("name_raw") or "-",
        avg,
        row.get("mention") or "-",
        row.get("decision") or row.get("decision_raw") or "-",
    ])


def build_pv_chunks(ocr_doc: dict[str, Any], institution_id: str | None, programme_id: str | None,
                    session_year: int | None, header: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Chunk dicts (ready for ``Chunk(**d)``) for a PV: one ``text`` chunk per page + one ``pv_row`` per row."""
    ctx = refdata.context_line(institution_id, programme_id, session_year)
    header = header or {}
    chunks: list[dict[str, Any]] = []
    cols: dict[str, float] | None = None
    page_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for page in ocr_doc["pages"]:
        rows, cols = extract_pv_rows(page, cols)
        page_rows.append((page, rows))
    repair_matricules([r for _, rows in page_rows for r in rows])
    for page, rows in page_rows:
        pno, size = page["page"], page["size"]
        txt = page_text(page["words"])
        if page["words"]:
            allb = [w["b"] for w in page["words"]]
            chunks.append({
                "kind": "text", "page": pno, "row_idx": None, "text": f"{ctx}\n{txt}" if ctx else txt,
                "fields": {"engine": page.get("engine"), "n_words": len(page["words"])},
                "bbox": [min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb)],
                "page_size": size, "ocr_conf": page.get("conf"),
            })
        for i, r in enumerate(rows):
            fields = {k: r[k] for k in ("row_n", "matricule", "name_raw", "name_norm", "average", "mention",
                                        "mention_raw", "decision", "decision_raw")}
            if r.get("matricule_raw"):
                fields["matricule_raw"] = r["matricule_raw"]
            if header.get("deliberation_date"):
                fields["deliberation_date"] = header["deliberation_date"]
            chunks.append({
                "kind": "pv_row", "page": pno, "row_idx": r["row_n"] if r["row_n"] is not None else i + 1,
                "text": f"{format_row_text(r)} | {ctx}" if ctx else format_row_text(r),
                "fields": fields, "bbox": r["bbox"], "page_size": size, "ocr_conf": r["ocr_conf"],
                "name_norm": r["name_norm"][:256],
            })
    return chunks


# ------------------------------------------------------------------------------------------------ list PDFs


def extract_list_rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Digital 'liste des admis' rows: N° | name | programme | mention (no matricule)."""
    lines = cluster_lines(page["words"])
    rows = []
    hdr_idx = None
    for i, ln in enumerate(lines):
        t = _fold(line_text(ln))
        if "nomet" in t and "mention" in t:
            hdr_idx = i
            xs = {"name": next((w["b"][0] for w in ln if _fold(w["t"]) == "nom"), None),
                  "prog": next((w["b"][0] for w in ln if _fold(w["t"]).startswith(("fili", "progr"))), None),
                  "mention": next((w["b"][0] for w in ln if _fold(w["t"]) in ("mention", "grade")), None)}
            break
    if hdr_idx is None or None in xs.values():
        return rows
    w_ = page["size"][0]
    for ln in lines[hdr_idx + 1:]:
        if not ln or not _is_row_start(ln[0]["t"]):
            continue
        name = " ".join(w["t"] for w in ln[1:] if xs["name"] - 4 <= w["b"][0] < xs["prog"] - 4)
        prog = " ".join(w["t"] for w in ln if xs["prog"] - 4 <= w["b"][0] < xs["mention"] - 4)
        ment = " ".join(w["t"] for w in ln if w["b"][0] >= xs["mention"] - 4)
        if not name:
            continue
        bb = line_bbox(ln)
        rows.append({"row_n": int(re.sub(r"\D", "", ln[0]["t"])), "name_raw": name, "name_norm": normalize_name(name),
                     "programme_text": prog, "mention": normalize_mention(ment), "mention_raw": ment or None,
                     "bbox": [bb[0] - 8, bb[1] - 4, bb[2] + 8, bb[3] + 4],
                     "ocr_conf": round(float(np.mean([w["c"] for w in ln])), 4)})
    return rows


def build_list_chunks(ocr_doc: dict[str, Any], institution_id: str | None, session_year: int | None) -> list[dict[str, Any]]:
    chunks = []
    for page in ocr_doc["pages"]:
        txt = page_text(page["words"])
        ctx = refdata.context_line(institution_id, None, session_year, "liste des admis")
        if page["words"]:
            allb = [w["b"] for w in page["words"]]
            chunks.append({"kind": "text", "page": page["page"], "row_idx": None, "text": f"{ctx}\n{txt}",
                           "fields": {"engine": page.get("engine")},
                           "bbox": [min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb)],
                           "page_size": page["size"], "ocr_conf": page.get("conf")})
        for r in extract_list_rows(page):
            pid, _ = refdata.match_programme(r["programme_text"], institution_id)
            avg_txt = f"{r['name_raw']} | {r['programme_text']} | {r['mention'] or '-'}"
            chunks.append({
                "kind": "list_row", "page": page["page"], "row_idx": r["row_n"],
                "text": f"N° {r['row_n']} | {avg_txt} | {ctx}",
                "fields": {"row_n": r["row_n"], "name_raw": r["name_raw"], "name_norm": r["name_norm"],
                           "programme": pid or r["programme_text"], "mention": r["mention"], "decision": "Admis"},
                "bbox": r["bbox"], "page_size": page["size"], "ocr_conf": r["ocr_conf"],
                "programme_id": pid, "name_norm": r["name_norm"][:256]})
    return chunks


def build_text_chunks(ocr_doc: dict[str, Any], context: str = "") -> list[dict[str, Any]]:
    """Page-level text chunks only (diplomas, unknown scans)."""
    chunks = []
    for page in ocr_doc["pages"]:
        if not page["words"]:
            continue
        allb = [w["b"] for w in page["words"]]
        txt = page_text(page["words"])
        chunks.append({"kind": "text", "page": page["page"], "row_idx": None,
                       "text": f"{context}\n{txt}" if context else txt, "fields": {"engine": page.get("engine")},
                       "bbox": [min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb)],
                       "page_size": page["size"], "ocr_conf": page.get("conf")})
    return chunks


def make_searchable_pdf(src: str | Path, dst: str | Path | None = None) -> Path:
    """Optional archival artefact: a searchable PDF via OCRmyPDF (``-l fra+eng --deskew --rotate-pages``, plus
    ``--clean`` when unpaper is installed). Not used for evidence coordinates (those come from ``ocr_document`` so
    that bboxes live in the pixel space of the cached page raster)."""
    try:
        import ocrmypdf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("ocrmypdf is not installed") from exc
    src = Path(src)
    dst = Path(dst) if dst else _cache_dir("searchable") / f"{sha256_file(src)}.pdf"
    if not dst.is_file():
        ocrmypdf.ocr(str(src), str(dst), language=["fra", "eng"], deskew=True, rotate_pages=True,
                     clean=bool(shutil.which("unpaper")), force_ocr=True, progress_bar=False)
    return dst


# ------------------------------------------------------------------------------------------------ crops


def crop_png(img: np.ndarray, bbox: list[float], highlight: bool = True, pad: int = 12) -> bytes:
    """PNG crop of ``img`` around ``bbox`` (+pad) with an orange highlight rectangle."""
    from PIL import Image, ImageDraw

    h, w = img.shape[:2]
    x0, y0, x1, y1 = [float(v) for v in bbox]
    cx0, cy0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    cx1, cy1 = min(w, int(math.ceil(x1 + pad))), min(h, int(math.ceil(y1 + pad)))
    base = Image.fromarray(img).convert("RGB").crop((cx0, cy0, max(cx1, cx0 + 1), max(cy1, cy0 + 1)))
    if highlight:
        ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        rect = [x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0]
        d.rectangle(rect, fill=(255, 140, 0, 50), outline=(220, 30, 30, 255), width=3)
        base = Image.alpha_composite(base.convert("RGBA"), ov).convert("RGB")
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


def render_crop_from_path(path: str | Path, page: int, bbox: list[float], sha: str | None = None,
                          highlight: bool = True, pad: int = 12) -> bytes:
    return crop_png(get_page_image(path, sha, page), bbox, highlight, pad)


def render_crop(document_id: int, page: int, bbox: list[float], highlight: bool = True, pad: int = 12) -> bytes:
    """PNG of the region ``bbox`` of ``page`` of source document ``document_id`` with a highlighted rectangle.

    The page raster is the deskewed one used for OCR (same dpi/pixel space as ``Chunk.bbox``) and is cached under
    DATA_DIR/cache/pages/.
    """
    from app.core.db import SessionLocal
    from app.core.models import SourceDocument

    s = SessionLocal()
    try:
        doc = s.get(SourceDocument, document_id)
        if doc is None:
            raise LookupError(f"document {document_id} not found")
        path, sha = doc.path, doc.sha256
    finally:
        s.close()
    p = Path(path)
    if not p.is_absolute():
        p = data_dir().parent / p
    if p.suffix.lower() in (".csv", ".xlsx", ".xls"):
        raise ValueError("spreadsheet documents have no page image")
    return render_crop_from_path(p, sha=sha, page=page, bbox=bbox, highlight=highlight, pad=pad)
