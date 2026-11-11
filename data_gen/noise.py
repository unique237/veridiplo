"""Scan-like degradation: rasterise -> rotate -> blur -> noise -> JPEG artefacts -> image-only PDF.

Everything is driven by an explicit numpy Generator so output is reproducible.
"""
from __future__ import annotations

import hashlib
import io
from typing import Sequence

import cv2
import fitz  # PyMuPDF
import numpy as np
from reportlab import rl_config
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

rl_config.useA85 = 0  # embed JPEG as raw DCT (no slow, bloating ASCII85 wrapper)

# preset -> parameter ranges
PRESETS = {
    # archive PV scanned on an office scanner
    "scan": dict(rot=2.0, blur=(0.6, 1.0), noise=(2.0, 3.5), speck=0.0004, jpeg=(36, 48), illum=8, streak=0.3),
    # poor photocopy / old archive page
    "poor": dict(rot=2.0, blur=(1.1, 1.5), noise=(6.0, 8.0), speck=0.0012, jpeg=(26, 34), illum=16, streak=0.7),
    # mildly degraded diploma photo/scan
    "diploma": dict(rot=0.8, blur=(0.5, 0.8), noise=(1.0, 1.8), speck=0.0001, jpeg=(65, 78), illum=3, streak=0.0),
}


def rng_for(seed: int, key: str) -> np.random.Generator:
    h = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "big"))


def rasterise_pdf(pdf_path: str, dpi: int, gray: bool = True) -> list[np.ndarray]:
    """Render each page of a PDF to a numpy array (H, W) uint8 (gray) or (H, W, 3)."""
    pages = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY if gray else fitz.csRGB, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            pages.append(arr[:, :, 0].copy() if gray else arr.copy())
    return pages


def _illumination(h: int, w: int, rng: np.random.Generator, amp: float) -> np.ndarray:
    small = rng.uniform(-1, 1, size=(5, 4)).astype(np.float32)
    big = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    return 1.0 + (amp / 255.0) * big


def degrade(img: np.ndarray, rng: np.random.Generator, preset: str = "scan") -> tuple[bytes, dict]:
    """Degrade an image (gray or BGR/RGB uint8). Returns (JPEG bytes, applied params)."""
    p = PRESETS[preset]
    h, w = img.shape[:2]
    f = img.astype(np.float32)
    # paper tone + uneven illumination
    f = 255.0 - (255.0 - f) * float(rng.uniform(0.9, 1.0))
    illum = _illumination(h, w, rng, p["illum"])
    f = f * (illum if f.ndim == 2 else illum[:, :, None]) * float(rng.uniform(0.95, 0.99))
    # rotation
    angle = float(rng.uniform(-p["rot"], p["rot"]))
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    f = cv2.warpAffine(f, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(232, 232, 232) if f.ndim == 3 else 232)
    # blur
    sigma = float(rng.uniform(*p["blur"]))
    f = cv2.GaussianBlur(f, (0, 0), sigma)
    # gaussian noise
    ns = float(rng.uniform(*p["noise"]))
    nz = rng.standard_normal(f.shape[:2], dtype=np.float32) * ns
    f = f + (nz if f.ndim == 2 else nz[:, :, None])  # luminance noise (keeps chroma clean, like a mono sensor)
    # pepper speckles
    n_sp = int(h * w * p["speck"])
    if n_sp:
        ys = rng.integers(0, h, n_sp)
        xs = rng.integers(0, w, n_sp)
        vals = rng.uniform(0, 90, n_sp).astype(np.float32)
        f[ys, xs] = vals[:, None] if f.ndim == 3 else vals
    # scanner streak
    streak = bool(rng.random() < p["streak"])
    if streak:
        x = int(rng.integers(int(w * 0.05), int(w * 0.95)))
        f[:, x:x + 1] *= float(rng.uniform(0.86, 0.95))
    f = np.clip(f, 0, 255).astype(np.uint8)
    q = int(rng.integers(p["jpeg"][0], p["jpeg"][1] + 1))
    ok, buf = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    assert ok
    return buf.tobytes(), {
        "preset": preset, "rotation_deg": round(angle, 3), "blur_sigma": round(sigma, 3),
        "noise_sigma": round(ns, 3), "jpeg_quality": q, "streak": streak,
    }


def jpegs_to_pdf(jpegs: Sequence[bytes], sizes_px: Sequence[tuple[int, int]], dpi: int, out_path: str,
                 title: str = "SPECIMEN - SYNTHETIC DATA") -> None:
    """Assemble JPEG pages into an image-only PDF (no text layer), deterministic output."""
    c = canvas.Canvas(out_path, pagesize=(sizes_px[0][0] * 72.0 / dpi, sizes_px[0][1] * 72.0 / dpi),
                      invariant=1, pageCompression=1)
    c.setTitle(title)
    c.setAuthor("VeriDiplo synthetic data generator")
    for jpg, (w, h) in zip(jpegs, sizes_px):
        wp, hp = w * 72.0 / dpi, h * 72.0 / dpi
        c.setPageSize((wp, hp))
        c.drawImage(ImageReader(io.BytesIO(jpg)), 0, 0, width=wp, height=hp)
        c.showPage()
    c.save()


def scan_pdf(clean_pdf: str, out_pdf: str, seed: int, key: str, dpi: int = 200, preset: str = "scan") -> dict:
    """Turn a text-layer PDF into a scanned-looking, image-only PDF. Returns applied params."""
    rng = rng_for(seed, key)
    pages = rasterise_pdf(clean_pdf, dpi, gray=True)
    jpegs, sizes, params = [], [], []
    for pg in pages:
        jpg, pr = degrade(pg, rng, preset)
        jpegs.append(jpg)
        sizes.append((pg.shape[1], pg.shape[0]))
        params.append(pr)
    jpegs_to_pdf(jpegs, sizes, dpi, out_pdf)
    return {"dpi": dpi, "pages": len(pages), "pages_params": params}


def degrade_to_png(clean_pdf: str, out_png: str, seed: int, key: str, dpi: int = 150, preset: str = "diploma") -> dict:
    """Rasterise page 1 of a PDF, mildly degrade it (incl. JPEG artefacts) and save as PNG."""
    rng = rng_for(seed, key)
    img = rasterise_pdf(clean_pdf, dpi, gray=False)[0][:, :, ::-1].copy()  # RGB -> BGR for OpenCV
    jpg, params = degrade(img, rng, preset)
    arr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)  # BGR
    ok, png = cv2.imencode(".png", arr, [int(cv2.IMWRITE_PNG_COMPRESSION), 4])
    assert ok
    with open(out_png, "wb") as fh:
        fh.write(png.tobytes())
    params.update({"dpi": dpi, "size_px": [int(arr.shape[1]), int(arr.shape[0])]})
    return params
