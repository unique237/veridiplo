#!/usr/bin/env python3
"""VeriDiplo knowledge-base builder: download -> extract -> chunk -> JSONL -> (optional) Postgres.

Standalone (stdlib + requests, beautifulsoup4, pyyaml, pdfplumber/pypdf). OCR (scanned PDFs) needs
`ocrmypdf` (preferred) or `tesseract` + `pdftoppm`; without them such sources are reported as `pending_ocr`.
Database loading (`load_to_db`) is optional and imported lazily (sqlalchemy + psycopg).

Usage (from repo root):
    python kb/build_kb.py                      # full build, idempotent, prints coverage table
    python kb/build_kb.py --offline            # re-chunk from kb/raw only (no network)
    python kb/build_kb.py --only CM-DP-2024    # one or more source ids (comma separated)
    python kb/build_kb.py --refresh            # re-download everything
    python kb/build_kb.py --retry-failed       # retry downloads that failed in an earlier run
    python kb/build_kb.py --load-db            # also load chunks into Postgres (FTS + pgvector)
    python kb/build_kb.py --sync-status        # write actual status/raw_file/chunks back into sources.yaml

Environment: KB_DIR (default: directory of this file) selects another knowledge base (domain swap).

NEVER fabricate content: a source whose text cannot be obtained stays `pending_download` and gets no chunks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import robotparser
from urllib.parse import urlparse

KB_DIR = Path(os.environ.get("KB_DIR") or Path(__file__).resolve().parent).resolve()
RAW_DIR = KB_DIR / "raw"
CHUNKS_DIR = KB_DIR / "chunks"
MANIFEST = RAW_DIR / "MANIFEST.json"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) VeriDiploKB/1.0 (hackathon research; polite crawler; honours robots.txt)"
TIMEOUT = (10, 90)
NODE_ORDER = ["SRC", "C", "PP", "M", "P", "D", "SINK"]
TARGET_WORDS = 330        # ~ 440 tokens
OVERLAP_WORDS = 40        # ~ 50 tokens
MAX_ARTICLE_WORDS = 520   # articles longer than this are split (~ 700 tokens)

BOT_CHALLENGE_MARKERS = ("Just a moment...", "Enable JavaScript and cookies to continue", "cf-browser-verification")


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold(s: str) -> str:
    """Lowercase + strip accents (for keyword matching)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def est_tokens(text: str) -> int:
    return int(len(text.split()) * 1.33)


def log(msg: str) -> None:
    print(msg, flush=True)


def _need(mod: str, pip: str | None = None):
    try:
        return __import__(mod)
    except ImportError:  # pragma: no cover
        sys.exit(f"Missing dependency '{mod}'. Install with: pip install {pip or mod}")


def load_yaml(p: Path) -> Any:
    yaml = _need("yaml", "pyyaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------------------
def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"description": "Download manifest of kb/raw (url, sha256, date retrieved, http status)", "entries": {}}


def save_manifest(m: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    m["entries"] = dict(sorted(m["entries"].items()))
    MANIFEST.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# download (robots-aware, timeout, retry once)
# --------------------------------------------------------------------------------------
_robots_cache: dict[tuple, robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}


def robots_allowed(url: str, session) -> bool:
    """robots.txt check. Python's parser mangles rules ending in '?' (e.g. W3C `Disallow: /TR/?`), so such
    query-only rules are dropped for URLs that carry no query string."""
    u = urlparse(url)
    base = f"{u.scheme}://{u.netloc}"
    key = (base, bool(u.query))
    if key not in _robots_cache:
        rp = robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt", timeout=(5, 15))
            if r.status_code == 200 and "text" in r.headers.get("content-type", "text"):
                lines = r.text.splitlines()
                if not u.query:
                    lines = [l for l in lines if not re.match(r"(?i)\s*(dis)?allow:\s*\S*\?\s*$", l)]
                rp.parse(lines)
                _robots_cache[key] = rp
            else:
                _robots_cache[key] = None  # no robots.txt (or unreadable) -> allowed
        except Exception:
            _robots_cache[key] = None
    rp = _robots_cache[key]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def guess_ext(url: str, content_type: str, head: bytes) -> str:
    if head.startswith(b"%PDF"):
        return "pdf"
    if url.lower().split("?")[0].endswith(".json") or "json" in content_type:
        return "json"
    return "html"


def download(url: str, stem: str, manifest: dict, session, refresh=False, retry_failed=False) -> dict:
    """Download url to kb/raw/<stem>.<ext>; returns the manifest entry (ok or failed)."""
    key_prefix = stem
    # reuse previous result
    for k, e in manifest["entries"].items():
        if e.get("url") == url and e.get("stem") == key_prefix and not refresh:
            if e.get("ok") and (RAW_DIR / e["raw_file"]).exists():
                return e
            if not e.get("ok") and not retry_failed:
                return e
    host = urlparse(url).netloc
    wait = 0.7 - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    entry: dict = {"stem": stem, "url": url, "ok": False, "origin": "download", "retrieved": now_iso()}
    if not robots_allowed(url, session):
        entry.update(error="disallowed by robots.txt", http_status=None)
        manifest["entries"][f"{stem}|{url}"] = entry
        return entry
    last_err = None
    for attempt in (1, 2):
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            _last_hit[host] = time.time()
            entry["http_status"] = r.status_code
            entry["content_type"] = r.headers.get("content-type", "")
            if r.status_code in (429,) or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                time.sleep(2)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                if r.status_code == 403:
                    last_err += " (access refused / bot protection)"
                break
            body = r.content
            if any(m.encode() in body[:6000] for m in BOT_CHALLENGE_MARKERS):
                last_err = "bot-challenge page (not the document)"
                break
            if len(body) < 300:
                last_err = f"suspiciously small response ({len(body)} bytes)"
                break
            ext = guess_ext(url, entry["content_type"], body[:8])
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            fn = f"{stem}.{ext}"
            (RAW_DIR / fn).write_bytes(body)
            entry.update(ok=True, raw_file=fn, sha256=sha256_bytes(body), bytes=len(body), final_url=r.url)
            entry.pop("error", None)
            manifest["entries"][f"{stem}|{url}"] = entry
            return entry
        except Exception as e:  # network error, TLS, timeout
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(2)
    entry["error"] = last_err
    manifest["entries"][f"{stem}|{url}"] = entry
    return entry


# --------------------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------------------
class Doc:
    """Extracted text with optional page offsets."""

    def __init__(self, text: str, page_starts: list[tuple[int, int]] | None = None, method: str = "text",
                 url: str | None = None, raw_file: str | None = None, kind_override: str | None = None):
        self.text = text
        self.page_starts = page_starts or []
        self.method = method
        self.url = url
        self.raw_file = raw_file
        self.kind_override = kind_override

    def page_at(self, offset: int) -> int | None:
        if not self.page_starts:
            return None
        pg = self.page_starts[0][1]
        for off, p in self.page_starts:
            if off <= offset:
                pg = p
            else:
                break
        return pg


def tidy_text(t: str) -> str:
    t = unicodedata.normalize("NFC", t).replace("\x0c", "\n").replace(" ", " ").replace("​", "")
    t = t.replace("­", "")  # soft hyphen
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = re.sub(r"(\w)-\n([a-zàâçéèêëîïôûùüÿœ])", r"\1\2", t)  # de-hyphenate
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_pages(pages: list[str], junk_res: list[str], page_nums: list[int] | None = None) -> tuple[str, list[tuple[int, int]]]:
    """Remove running headers/footers, page numbers, TOC dot leaders, watermark junk; join pages."""
    norm = lambda l: re.sub(r"\d+", "#", re.sub(r"\s+", " ", l.strip()))
    counts: dict[str, int] = {}
    for p in pages:
        for l in {norm(x) for x in p.splitlines() if x.strip()}:
            counts[l] = counts.get(l, 0) + 1
    n = max(len(pages), 1)
    repeated = {l for l, c in counts.items() if c >= 3 and c >= 0.25 * n and len(l) < 120 and len(l) > 2}
    junk = [re.compile(j) for j in junk_res]
    out, starts, off = [], [], 0
    for i, p in enumerate(pages, 1):
        keep = []
        for line in p.splitlines():
            s = line.strip()
            if not s:
                keep.append("")
                continue
            if norm(s) in repeated and not re.match(r"^(ARTICLE|Article|ART\.|Art\.)\s*\d", s):
                continue
            if re.fullmatch(r"[-–]?\s*\d{1,3}\s*[-–]?", s):
                continue
            if re.search(r"\.{6,}", s) or re.search(r"(\. ){6,}", s):
                continue
            if any(j.search(s) for j in junk):
                continue
            keep.append(line)
        txt = tidy_text("\n".join(keep))
        starts.append((off, page_nums[i - 1] if page_nums else i))
        out.append(txt)
        off += len(txt) + 2
    return "\n\n".join(out), starts


def _rasterise(path: Path, outdir: Path, dpi: int = 300) -> list[Path]:
    """PDF -> grayscale PNG pages (ghostscript, which ocrmypdf already requires; pdftoppm as fallback)."""
    if shutil.which("gs"):
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pnggray", f"-r{dpi}",
                        f"-sOutputFile={outdir}/p-%03d.png", str(path)], check=False)
    elif shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", str(path), str(outdir / "p")], check=False)
    return sorted(outdir.glob("p-*.png"))


def ocr_pdf(path: Path, lang: str, cache_json: Path, sha: str, threshold: int | None = None) -> list[str] | None:
    """OCR a PDF page by page. Returns list of page texts or None if no OCR engine is available.

    threshold: if set (e.g. 140), pages are rasterised, binarised with Pillow (removes grey watermarks such as the
    diagonal 'www.prc.cm' of the Presidency's certified copies) and OCR'd with the tesseract CLI.
    """
    tag = f"{lang}|thr={threshold}"
    if cache_json.exists():
        try:
            c = json.loads(cache_json.read_text(encoding="utf-8"))
            if c.get("sha256") == sha and c.get("lang") in (tag, lang if not threshold else tag):
                return c["pages"]
        except Exception:
            pass
    pages: list[str] | None = None
    if threshold and shutil.which("tesseract"):
        try:
            from PIL import Image  # type: ignore
            with tempfile.TemporaryDirectory() as td:
                log(f"    OCR (tesseract {lang}, threshold {threshold}) {path.name} ...")
                imgs = _rasterise(path, Path(td))
                pages = []
                for img in imgs:
                    im = Image.open(img).convert("L").point(lambda v: 255 if v > threshold else 0).convert("1")
                    bw = img.with_suffix(".bw.png")
                    im.save(bw)
                    r = subprocess.run(["tesseract", str(bw), "-", "-l", lang, "--psm", "4"], capture_output=True, text=True)
                    pages.append(r.stdout)
                if not pages:
                    pages = None
        except ImportError:
            log("    Pillow missing: falling back to plain OCR")
    if pages is None and shutil.which("ocrmypdf"):
        with tempfile.TemporaryDirectory() as td:
            side, outpdf = Path(td) / "side.txt", Path(td) / "out.pdf"
            cmd = ["ocrmypdf", "--force-ocr", "-l", lang, "--sidecar", str(side), "--output-type", "pdf",
                   "--jobs", "2", "-q", str(path), str(outpdf)]
            log(f"    OCR (ocrmypdf {lang}) {path.name} ...")
            r = subprocess.run(cmd, capture_output=True, text=True)
            if side.exists() and r.returncode in (0, 6):
                pages = side.read_text(encoding="utf-8", errors="replace").split("\f")
                if pages and not pages[-1].strip():
                    pages = pages[:-1]
            else:
                log(f"    ocrmypdf failed (rc={r.returncode}): {r.stderr[:200]}")
    if pages is None and shutil.which("tesseract") and (shutil.which("gs") or shutil.which("pdftoppm")):
        with tempfile.TemporaryDirectory() as td:
            log(f"    OCR (tesseract {lang}) {path.name} ...")
            pages = []
            for img in _rasterise(path, Path(td)):
                r = subprocess.run(["tesseract", str(img), "-", "-l", lang], capture_output=True, text=True)
                pages.append(r.stdout)
    if pages is None:
        return None
    cache_json.write_text(json.dumps({"sha256": sha, "lang": tag, "pages": pages}, ensure_ascii=False), encoding="utf-8")
    return pages


def extract_pdf(path: Path, src: dict, no_ocr: bool) -> Doc | None:
    ex = src.get("extract") or {}
    mode = ex.get("ocr", "auto")
    pages_txt: list[str] = []
    method = "pdf_text"
    if mode != "force":
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(path) as pdf:
                pages_txt = [(pg.extract_text() or "") for pg in pdf.pages]
        except Exception as e:
            log(f"    pdfplumber failed ({type(e).__name__}); trying pypdf")
            try:
                from pypdf import PdfReader  # type: ignore
                pages_txt = [(pg.extract_text() or "") for pg in PdfReader(str(path)).pages]
            except Exception as e2:
                log(f"    pypdf failed too: {e2}")
    avg = (sum(len(p.strip()) for p in pages_txt) / max(len(pages_txt), 1)) if pages_txt else 0
    need_ocr = mode == "force" or (mode == "auto" and avg < 150)
    if need_ocr and mode != "never":
        if no_ocr:
            return None
        lang_default = "fra+eng" if src.get("lang") == "fr" else "eng"
        lang = ex.get("ocr_lang", lang_default)
        sha = sha256_file(path)
        ocr_pages = ocr_pdf(path, lang, path.with_suffix(".ocr.json"), sha, ex.get("ocr_threshold"))
        if ocr_pages is None:
            return None
        pages_txt, method = ocr_pages, "ocr"
    page_nums = None
    if ex.get("pages"):  # keep only some pages, e.g. [[1, 2], [12, 22]] (1-based, inclusive) — original page numbers are preserved
        rngs = ex["pages"] if isinstance(ex["pages"][0], list) else [ex["pages"]]
        page_nums = [i for i in range(1, len(pages_txt) + 1) if any(a <= i <= b for a, b in rngs)]
        pages_txt = [pages_txt[i - 1] for i in page_nums]
    text, starts = clean_pages(pages_txt, ex.get("junk_lines") or [], page_nums)
    return Doc(text, starts, method)


BLOCK_LEAF = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dt", "dd", "caption", "tr", "div", "td", "th", "section"]
BLOCK_INNER = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dt", "dd", "table", "ul", "ol", "div", "tr", "section"]
NOISE_ATTR = re.compile(r"cookie|breadcrumb|sidebar|menu|share|advert|newsletter|social|banner|skip", re.I)


def extract_html(path: Path, src: dict) -> Doc:
    bs4 = _need("bs4", "beautifulsoup4")
    import warnings
    from bs4 import BeautifulSoup, UnicodeDammit, XMLParsedAsHTMLWarning  # type: ignore
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    raw = path.read_bytes()
    html = UnicodeDammit(raw, is_html=True).unicode_markup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    ex = src.get("extract") or {}
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "iframe", "select"]):
        t.decompose()
    for el in list(soup.find_all(True)):
        if getattr(el, "attrs", None) is None:
            continue
        ident = " ".join(el.get("class", []) if isinstance(el.get("class"), list) else [str(el.get("class") or "")]) + " " + str(el.get("id") or "")
        if el.name not in ("html", "body", "main", "article") and NOISE_ATTR.search(ident):
            el.decompose()
    root = None
    sel = ex.get("selector")
    if sel:
        root = soup.select_one(sel)
    if root is None:
        root = soup.find("article") or soup.find("main") or soup.body or soup
    for br in root.find_all("br"):
        br.replace_with("\n")
    lines: list[str] = []
    for el in root.find_all(BLOCK_LEAF):
        if getattr(el, "attrs", None) is None:
            continue
        if el.name != "tr" and el.find_parent("tr") is not None:
            continue
        if el.name in ("div", "td", "th", "li", "blockquote", "dd", "dt", "section") and el.find(BLOCK_INNER):
            continue
        if el.name == "tr":
            cells = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])]
            text = " ".join(c for c in cells if c)
        else:
            text = el.get_text("", strip=False)
        text = re.sub(r"[ \t\r\f\v ]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text).strip()
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = "\n## " + re.sub(r"\s+", " ", text)
        if lines and lines[-1] == text:
            continue
        lines.append(text)
    text = tidy_text("\n".join(lines))
    for j in ex.get("junk_lines") or []:
        text = "\n".join(l for l in text.split("\n") if not re.search(j, l))
    for cut in ex.get("cut_after") or []:  # drop page boilerplate (related stories, footers) after a marker
        m = re.search(cut, text)
        if m:
            text = text[: m.start()].rstrip()
    return Doc(text, [], "html")


def extract_doc(path: Path, src: dict, no_ocr: bool) -> Doc | None:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path, src, no_ocr)
    if ext == ".json":
        return Doc(path.read_text(encoding="utf-8"), [], "json")
    if ext in (".txt", ".md"):
        return Doc(tidy_text(path.read_text(encoding="utf-8", errors="replace")), [], "text")
    if ext in (".html", ".htm"):
        return extract_html(path, src)
    log(f"    unsupported file type {ext}: {path.name}")
    return None


# --------------------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------------------
WORD_RE = re.compile(r"\S+")
HEAD_LINE_RE = re.compile(r"^\s*(CHAPITRE|CHAPTER|TITRE|TITLE|PARTIE|PART|SECTION|LIVRE|SOUS-SECTION)\b[ \t]*[\dIVXLC]+[^\n]*$", re.M)
ART_RE = re.compile(
    r"(?m)^[ \t]*(?:ARTICLE|Article|ART\.?|Art\.)[ \t]*(\d+(?:[ \t]*(?:er|bis|ter|quater))?(?:[ \t]*-[ \t]*\d+)?|er|1er|premier)(?![\d])(?P<rest>[^\n]*)"
)
SECTION_RE = re.compile(r"(?m)^[ \t]*(?:SECTION|Section)[ \t]+(\d+)(?P<rest>[^\n]*)")
ANNEX_RE = re.compile(r"(?m)^[ \t]*ANNEX[ \t]+([IVXL]+)[ \t]*$")


def window_spans(text: str, start: int, end: int, target=TARGET_WORDS, overlap=OVERLAP_WORDS) -> list[tuple[int, int]]:
    """Split text[start:end] into word windows (~target words, `overlap` words overlap), ending at sentence boundaries when possible."""
    words = [(m.start() + start, m.end() + start) for m in WORD_RE.finditer(text[start:end])]
    if not words:
        return []
    if len(words) <= target * 1.25:
        return [(words[0][0], words[-1][1])]
    spans, i = [], 0
    while i < len(words):
        j = min(i + target, len(words))
        if j < len(words):  # try to end at a sentence boundary within the last 60 words
            for k in range(j, max(i + target - 60, i + 1), -1):
                w = text[words[k - 1][0]:words[k - 1][1]]
                if w.endswith((".", ";", ":", "?", "!")):
                    j = k
                    break
        spans.append((words[i][0], words[j - 1][1]))
        if j >= len(words):
            break
        i = max(j - overlap, i + 1)
    return spans


def _norm_artnum(raw: str) -> str:
    n = re.sub(r"\s+", "", raw)
    if n.lower() in ("er", "premier"):
        return "1"
    if re.fullmatch(r"[IVXl!{|]+\.\d+", n):  # roman article numbers (Addis Ababa Convention: IV.3), OCR-tolerant
        n = re.sub(r"[l!{|]", "I", n)
    n = re.sub(r"(?i)er$", "", n)
    return n


def _valid_heading_rest(rest: str) -> bool:
    r = rest.strip()
    if not r:
        return True
    if re.match(r"^[.\-–—:]", r):
        return True
    return not re.match(r"^[a-zà-ÿ(]", r)


def segment_articles(text: str, src: dict) -> list[dict]:
    ch = src.get("chunking") or {}
    rx = re.compile(ch["article_regex"]) if ch.get("article_regex") else ART_RE
    marks = []
    for m in rx.finditer(text):
        rest = m.groupdict().get("rest", "") or ""
        if not _valid_heading_rest(rest) or len(m.group(0)) > 220:
            continue
        marks.append((m.start(), f"Art. {_norm_artnum(m.group(1))}", _norm_artnum(m.group(1))))
    if ch.get("section_as_article"):
        for m in SECTION_RE.finditer(text):
            if _valid_heading_rest(m.groupdict().get("rest", "") or ""):
                marks.append((m.start(), f"Section {m.group(1)}", m.group(1)))
    for m in ANNEX_RE.finditer(text):
        marks.append((m.start(), f"Annex {m.group(1)}", f"Annex {m.group(1)}"))
    marks.sort()
    segs = []
    pre_end = marks[0][0] if marks else len(text)
    segs.append({"label": None, "num": None, "start": 0, "end": pre_end, "section": None})
    for i, (st, label, num) in enumerate(marks):
        en = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        segs.append({"label": label, "num": num, "start": st, "end": en, "section": None})
    # cut trailing chapter/section headings off article bodies and carry them to the next article
    carry = None
    for s in segs:
        s["section"] = carry
        body = text[s["start"]:s["end"]]
        lines = body.split("\n")
        cut_idx = None
        for idx in range(max(len(lines) - 6, 0), len(lines)):
            if HEAD_LINE_RE.match(lines[idx]) and idx > 0:
                cut_idx = idx
                break
        if cut_idx is not None:
            tail = [l.strip() for l in lines[cut_idx:] if l.strip()]
            carry = " — ".join(tail)[:160]
            s["end"] = s["start"] + len("\n".join(lines[:cut_idx]))
    # drop TOC-like stubs when the same article appears later with a body
    final = []
    for i, s in enumerate(segs):
        body = text[s["start"]:s["end"]].strip()
        if s["label"] and len(body) < 50 and any(t["num"] == s["num"] for t in segs[i + 1:]):
            continue
        final.append(s)
    return final


def chunk_articles(doc: Doc, src: dict) -> list[dict]:
    ch = src.get("chunking") or {}
    keep = {str(x) for x in ch["articles_keep"]} if ch.get("articles_keep") else None
    text = doc.text
    out = []
    seen_keep: set[str] = set()
    for s in segment_articles(text, src):
        body = text[s["start"]:s["end"]].strip()
        if s["label"] is None:
            if keep is not None or ch.get("preamble") == "skip" or len(body.split()) < 30:
                continue
            for a, b in window_spans(text, s["start"], s["end"]):
                out.append({"article": None, "section": "Preamble", "start": a, "end": b, "part": None})
            continue
        if keep is not None and s["num"] not in keep:
            continue
        if keep is not None and s["num"] in seen_keep:
            continue  # first occurrence only (skips cross-references / duplicates)
        seen_keep.add(s["num"])
        if len(body.split()) <= MAX_ARTICLE_WORDS:
            out.append({"article": s["label"], "section": s["section"], "start": s["start"], "end": s["end"], "part": None})
        else:
            spans = window_spans(text, s["start"], s["end"])
            for k, (a, b) in enumerate(spans, 1):
                out.append({"article": s["label"], "section": s["section"], "start": a, "end": b, "part": f"{k}/{len(spans)}"})
    return out


def chunk_tokens(doc: Doc, src: dict) -> list[dict]:
    ch = src.get("chunking") or {}
    text = doc.text
    mc = ch.get("max_chars")
    if mc and len(text) > mc:
        cut = text.rfind(". ", 0, mc)
        text = text[: cut + 1 if cut > mc * 0.6 else mc]
    return [{"article": None, "section": None, "start": a, "end": b, "part": None} for a, b in window_spans(text, 0, len(text))]


def chunk_headings(doc: Doc, src: dict) -> list[dict]:
    ch = src.get("chunking") or {}
    text = doc.text
    if ch.get("heading_regex"):
        hre = re.compile(ch["heading_regex"], re.M)
        heads = [(m.start(), m.group(0).strip()) for m in hre.finditer(text)]
    else:
        heads = [(m.start(), m.group(0).replace("##", "").strip()) for m in re.finditer(r"(?m)^## .+$", text)]
    if not heads:
        return chunk_tokens(doc, src)
    segs = []
    for i, (st, h) in enumerate(heads):
        en = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        segs.append({"head": h, "start": st, "end": en})
    inc = [re.compile(p) for p in ch.get("include_sections") or []]
    max_words = int(ch.get("max_tokens", 500) / 1.33)
    out, pending = [], None
    for s in segs:
        if inc and not any(r.search(s["head"]) for r in inc):
            continue
        if pending is not None:  # merge tiny segments forward
            s = {"head": pending["head"], "start": pending["start"], "end": s["end"]}
            pending = None
        if len(text[s["start"]:s["end"]].split()) < 45 and not inc:
            pending = s
            continue
        m = re.match(r"^(\d+(?:\.\d+)*|[A-Z]\.\d+(?:\.\d+)*|Appendix [IVX]+)\s", s["head"])
        prefix = "Clause " if doc.page_starts else "Section "
        label = (prefix + m.group(1)) if m else s["head"][:70]
        spans = window_spans(text, s["start"], s["end"], target=max_words)
        for k, (a, b) in enumerate(spans, 1):
            out.append({"article": label, "section": s["head"][:120], "start": a, "end": b, "part": f"{k}/{len(spans)}" if len(spans) > 1 else None})
    if pending is not None:
        out.append({"article": pending["head"][:70], "section": pending["head"][:120], "start": pending["start"], "end": pending["end"], "part": None})
    return out


def chunk_dimensions(raw_json: str, src: dict) -> list[dict]:
    d = json.loads(raw_json)
    out = []
    facts = d.get("factors", [])
    txt = (f"ITU AI Readiness framework {d.get('framework_version')} — source: {d.get('framework_source')}. "
           f"{len(d.get('dimensions', []))} dimensions; factors: "
           + "; ".join(f"{f['name']} ({f['id']}): {f['description']}" for f in facts))
    out.append({"article": "Framework overview", "section": None, "text": txt, "dims": list(range(1, 14))})
    fname = {f["id"]: f["name"] for f in facts}
    for dim in d.get("dimensions", []):
        t = (f"Dimension {dim['id']} — {dim['name']} ({dim['short_name']}). {dim['description']} "
             f"Mapped factors: {', '.join(fname.get(x, x) for x in dim.get('mapped_factors', []))}. Metrics: "
             + "; ".join(f"{m['id']} {m['name']}: {m['description']}" for m in dim.get("metrics", [])))
        out.append({"article": f"Dimension {dim['id']}", "section": dim["name"], "text": t, "dims": [dim["id"]], "anchor": f"dim{dim['id']:02d}"})
    return out


# --------------------------------------------------------------------------------------
# tagging
# --------------------------------------------------------------------------------------
class Tagger:
    def __init__(self, path: Path):
        cfg = load_yaml(path) if path.exists() else {"topics": {}}
        self.topics = cfg.get("topics", {})
        self.kw = {k: [fold(w) for w in v.get("keywords", [])] for k, v in self.topics.items()}

    def tag(self, text: str, src: dict) -> tuple[list[str], list[str], list[int]]:
        f = fold(text)
        hits = {}
        for k, words in self.kw.items():
            n = sum(f.count(w) for w in words)
            if n:
                hits[k] = n
        top = [k for k, _ in sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))][:6]
        if not top:
            top = list(src.get("topics") or [])
        nodes = set()
        dims = set()
        for k in top:
            cfg = self.topics.get(k, {})
            nodes.update(cfg.get("nodes", []))
            dims.update(cfg.get("dims", []))
        if not nodes:
            nodes.update(src.get("nodes") or [])
        nodes_sorted = [n for n in NODE_ORDER if n in nodes]
        return top, nodes_sorted, sorted(dims)


def detect_lang(text: str) -> str:
    f = " " + fold(text[:4000]) + " "
    fr = sum(f.count(w) for w in (" le ", " la ", " les ", " des ", " du ", " de ", " et ", " est ", " que ", " pour "))
    en = sum(f.count(w) for w in (" the ", " of ", " and ", " to ", " is ", " that ", " for ", " shall "))
    return "fr" if fr > en else "en"


# --------------------------------------------------------------------------------------
# per-source processing
# --------------------------------------------------------------------------------------
def source_docs(src: dict, manifest: dict, session, args) -> tuple[list[Doc], str, list[str]]:
    """Return (docs, effective_kind, notes). Order: local drop-in > primary_url > fallback_urls > secondary_urls (summary only)."""
    sid = src["id"]
    notes: list[str] = []
    kind = src.get("kind", "primary")
    docs: list[Doc] = []

    def extract_entry(e: dict, kind_override: str | None = None) -> Doc | None:
        p = RAW_DIR / e["raw_file"]
        d = extract_doc(p, src, args.no_ocr)
        if d is None:
            notes.append(f"{e['raw_file']}: extraction failed or OCR unavailable")
            return None
        d.url, d.raw_file, d.kind_override = e.get("url"), e["raw_file"], kind_override
        return d

    # 1. local drop-in (human-provided file named <id>.<ext>, not produced by our downloader)
    explicit = src.get("local_file")
    cands = [RAW_DIR / explicit] if explicit else sorted(RAW_DIR.glob(f"{sid}.*"))
    for p in cands:
        if not p.exists() or p.name.endswith(".ocr.json"):
            continue
        sha = sha256_file(p)
        known = [e for e in manifest["entries"].values() if e.get("raw_file") == p.name and e.get("sha256") == sha]
        if known and not explicit:
            continue  # it is our own download, handled below
        manifest["entries"][f"{sid}|manual:{p.name}"] = {"stem": sid, "url": None, "ok": True, "origin": "manual", "raw_file": p.name,
                                                        "sha256": sha, "bytes": p.stat().st_size, "retrieved": now_iso(), "http_status": None}
        d = extract_entry(manifest["entries"][f"{sid}|manual:{p.name}"])
        if d:
            notes.append(f"manual drop-in {p.name}")
            overview = p.suffix.lower() in (".html", ".htm") and bool(src.get("fallback_urls"))
            return [d], ("summary" if overview else kind), notes

    if args.offline:
        # use whatever our earlier downloads left in kb/raw
        got = [e for e in manifest["entries"].values() if e.get("stem", "").split("__")[0] == sid and e.get("ok") and (RAW_DIR / e["raw_file"]).exists()]
        for e in sorted(got, key=lambda x: x["raw_file"]):
            if e["raw_file"].endswith(".ocr.json"):
                continue
            d = extract_entry(e)
            if d:
                docs.append(d)
        if docs:
            fb = set(src.get("fallback_urls") or []) | (set(src.get("secondary_urls") or []) if kind == "summary" else set())
            used_fb = all(d.url in fb for d in docs) and src.get("primary_url") not in [d.url for d in docs]
            return docs, ("summary" if used_fb else kind), notes
        notes.append("offline: nothing in kb/raw")
        return [], kind, notes

    # 2. primary URL
    if src.get("primary_url") and kind != "summary":
        e = download(src["primary_url"], sid, manifest, session, args.refresh, args.retry_failed)
        if e.get("ok"):
            d = extract_entry(e)
            if d:
                return [d], kind, notes
        else:
            notes.append(f"primary failed: {e.get('error')}")
    # 3. fallback URLs (summary only)
    fbs = list(src.get("fallback_urls") or [])
    if src.get("primary_url") and kind == "summary":
        fbs = [src["primary_url"]] + fbs
    if fbs:
        for i, u in enumerate(fbs):
            e = download(u, f"{sid}__fb{i + 1}", manifest, session, args.refresh, args.retry_failed)
            if e.get("ok"):
                d = extract_entry(e, "summary")
                if d:
                    docs.append(d)
            else:
                notes.append(f"fallback failed ({u[:60]}): {e.get('error')}")
        if docs:
            return docs, "summary", notes
    # 4. secondary URLs (only for kind: summary)
    if kind == "summary":
        for i, u in enumerate(src.get("secondary_urls") or []):
            e = download(u, f"{sid}__{i + 1}", manifest, session, args.refresh, args.retry_failed)
            if e.get("ok"):
                d = extract_entry(e, "summary")
                if d:
                    docs.append(d)
            else:
                notes.append(f"secondary failed ({u[:60]}): {e.get('error')}")
        if docs:
            return docs, "summary", notes
    return [], kind, notes


def build_chunks(src: dict, docs: list[Doc], eff_kind: str, tagger: Tagger) -> list[dict]:
    sid = src["id"]
    ch = src.get("chunking") or {}
    mode = ch.get("mode", "tokens")
    recs: list[dict] = []
    for doc in docs:
        kind_doc = doc.kind_override or eff_kind
        if mode == "dimensions" and doc.method == "json":
            for it in chunk_dimensions(doc.text, src):
                recs.append({"article": it["article"], "section": it["section"], "text": it["text"], "page": None, "part": None,
                             "url": doc.url, "method": "json", "dims": it["dims"], "anchor": it.get("anchor"), "kind": kind_doc})
            continue
        if mode == "article" and kind_doc != "summary":
            spans = chunk_articles(doc, src)
            if not spans:  # no article structure found -> fall back to tokens
                spans = chunk_tokens(doc, src)
        elif mode == "headings" and kind_doc != "summary":
            spans = chunk_headings(doc, src)
        else:
            spans = chunk_tokens(doc, src)
        for sp in spans:
            txt = re.sub(r"(?m)^## ", "", doc.text[sp["start"]:sp["end"]]).strip()
            if len(txt.split()) < 8:
                continue
            recs.append({"article": sp["article"], "section": sp.get("section"), "text": txt, "page": doc.page_at(sp["start"]),
                         "part": sp.get("part"), "url": doc.url, "method": doc.method, "kind": kind_doc})
    mc = ch.get("max_chunks")
    if mc:
        recs = recs[: int(mc)]
    out = []
    lang_src = src.get("lang")
    for i, r in enumerate(recs, 1):
        topics, nodes, dims = tagger.tag(r["text"], src)
        if r.get("dims"):
            dims = r["dims"]
            topics = ["readiness_framework"]
        kind = r["kind"]
        cid = f"{sid}#c{i:03d}"
        rec = {
            "chunk_id": cid, "source_id": sid, "title": src["title"], "issuer": src.get("issuer"),
            "date": src.get("date"), "jurisdiction": src.get("jurisdiction"), "binding": bool(src.get("binding")),
            "kind": kind, "article": r["article"],
            "as_reported": bool(src.get("as_reported", False)) or (kind == "summary"),
            "lang": lang_src or detect_lang(r["text"]), "topics": topics, "pipeline_nodes": nodes or list(src.get("nodes") or []),
            "readiness_dims": dims, "url": r["url"], "text": r["text"], "sha256": sha256_bytes(r["text"].encode("utf-8")),
            "section": r.get("section"), "page": r.get("page"), "part": r.get("part"), "extraction": r["method"],
            "licence": src.get("licence"), "anchors": [],
        }
        if r.get("anchor"):
            rec["anchors"].append(r["anchor"])
        out.append(rec)
    # anchors from sources.yaml: only created when a regex matches the text of a real chunk
    for name, spec in (src.get("anchors") or {}).items():
        pat, art = (spec, None) if isinstance(spec, str) else (spec.get("pattern"), spec.get("article"))
        rx = re.compile(pat, re.I | re.S)
        for rec in out:
            if art and rec["article"] != art:
                continue
            if rx.search(rec["text"]):
                if name not in rec["anchors"]:
                    rec["anchors"].append(name)
                break
    return out


def write_chunks(sid: str, chunks: list[dict]) -> None:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    p = CHUNKS_DIR / f"{sid}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def collect_anchors() -> tuple[dict[str, str], list[str]]:
    """Rebuild anchors.json from all chunk files on disk; return (anchors, unresolved declared anchors)."""
    anchors: dict[str, str] = {}
    for p in sorted(CHUNKS_DIR.glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                c = json.loads(line)
                for a in c.get("anchors", []):
                    anchors[f"{c['source_id']}#{a}"] = c["chunk_id"]
    (CHUNKS_DIR / "anchors.json").write_text(json.dumps(dict(sorted(anchors.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return anchors, []


# --------------------------------------------------------------------------------------
# status sync into sources.yaml (text edit, keeps comments)
# --------------------------------------------------------------------------------------
def sync_status(sources_path: Path, results: dict[str, dict]) -> None:
    lines = sources_path.read_text(encoding="utf-8").split("\n")
    out, cur, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^  - id: (\S+)", line)
        if m:
            cur = m.group(1)
        if cur in results and re.match(r"^    (raw_file|chunks):", line):
            i += 1
            continue  # drop old auto lines, re-added after status
        if cur in results and re.match(r"^    status:", line):
            r = results[cur]
            out.append(f"    status: {r['status']}")
            if r.get("raw_file"):
                out.append(f"    raw_file: {r['raw_file']}")
            out.append(f"    chunks: {r['chunks']}")
            i += 1
            continue
        out.append(line)
        i += 1
    sources_path.write_text("\n".join(out), encoding="utf-8")


# --------------------------------------------------------------------------------------
# database loading (optional)
# --------------------------------------------------------------------------------------
def iter_chunks(chunks_dir: Path | None = None):
    for p in sorted((chunks_dir or CHUNKS_DIR).glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def _embed(texts: list[str]):
    """Local multilingual-e5-small embeddings (384-d). Returns None when the model is unavailable (FTS-only load)."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        model = SentenceTransformer(os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-small"))
        return model.encode(["passage: " + t for t in texts], normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    except Exception as e:
        log(f"  [load_to_db] embeddings skipped ({type(e).__name__}: {str(e)[:80]}); FTS only")
        return None


def load_to_db(engine=None, dsn: str | None = None, chunks_dir: Path | None = None, embed: bool = True) -> dict:
    """Load all chunks (kb/chunks/*.jsonl) into Postgres through the project's ORM (`app.core.models.KBChunk`).

    Idempotent (merge by primary key chunk_id; chunks of a source that disappeared from its JSONL are deleted).
    No table is created here: call `app.core.db.init_db()` first (it creates the vector extension, tables and the
    FTS index `ix_kbchunks_fts`). Embeddings (multilingual-e5-small, 384-d) are computed locally when
    sentence-transformers is installed, otherwise the column stays NULL (FTS-only search still works).

    engine: optional SQLAlchemy engine; default `app.core.db.get_engine()`; else `dsn` / $DATABASE_URL.
    """
    from sqlalchemy.orm import Session  # lazy: JSONL export never needs it

    try:
        from app.core.models import KBChunk
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("load_to_db needs the project package (run from the repo root with app/ on sys.path)") from e
    if engine is None:
        try:
            from app.core.db import get_engine
            engine = get_engine()
        except Exception:
            from sqlalchemy import create_engine
            url = dsn or os.environ.get("DATABASE_URL")
            if not url:
                raise RuntimeError("No database: pass engine=, dsn= or set DATABASE_URL")
            engine = create_engine(url)
    chunks = list(iter_chunks(chunks_dir))
    embs = _embed([c["text"] for c in chunks]) if (embed and chunks) else None
    cols = {c.name for c in KBChunk.__table__.columns} - {"embedding"}
    by_src: dict[str, list[str]] = {}
    with Session(engine) as ses:
        for i, c in enumerate(chunks):
            row = {k: c.get(k) for k in cols}
            row["topics"] = c.get("topics") or []
            row["pipeline_nodes"] = c.get("pipeline_nodes") or []
            row["readiness_dims"] = c.get("readiness_dims") or []
            obj = ses.get(KBChunk, c["chunk_id"])
            if obj is None:
                obj = KBChunk(**row)
                ses.add(obj)
            else:
                for k, v in row.items():
                    setattr(obj, k, v)
            if embs is not None:
                obj.embedding = [float(x) for x in embs[i]]
            by_src.setdefault(c["source_id"], []).append(c["chunk_id"])
        for sid, ids in by_src.items():
            ses.query(KBChunk).filter(KBChunk.source_id == sid, KBChunk.chunk_id.notin_(ids)).delete(synchronize_session=False)
        ses.commit()
    return {"chunks": len(chunks), "sources": len(by_src), "vector": embs is not None}


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def status_for(src: dict, docs: list[Doc], chunks: list[dict], eff_kind: str, notes: list[str], args) -> str:
    if chunks:
        return "downloaded" if eff_kind != "summary" else "summary_only"
    if any("OCR unavailable" in n for n in notes):
        return "pending_ocr"
    return "pending_download"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="no network: rebuild chunks from kb/raw")
    ap.add_argument("--refresh", action="store_true", help="re-download everything")
    ap.add_argument("--retry-failed", action="store_true", help="retry downloads that failed before")
    ap.add_argument("--no-ocr", action="store_true", help="skip OCR (scanned sources become pending_ocr)")
    ap.add_argument("--only", default="", help="comma-separated source ids")
    ap.add_argument("--load-db", action="store_true", help="load chunks into Postgres (needs sqlalchemy + DATABASE_URL)")
    ap.add_argument("--no-embed", action="store_true", help="with --load-db: skip embeddings")
    ap.add_argument("--sync-status", action="store_true", help="write actual status/raw_file/chunks into sources.yaml")
    args = ap.parse_args(argv)

    cfg = load_yaml(KB_DIR / "sources.yaml")
    sources = cfg["sources"]
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    manifest = load_manifest()
    tagger = Tagger(KB_DIR / "tagging.yaml")
    requests = _need("requests")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    for src in sources:
        sid = src["id"]
        if only and sid not in only:
            continue
        log(f"[{sid}]")
        docs, eff_kind, notes = source_docs(src, manifest, session, args)
        chunks = build_chunks(src, docs, eff_kind, tagger) if docs else []
        # vendoring (e.g. ITU readiness dimensions.json)
        if src.get("vendor_to") and docs and docs[0].raw_file:
            dst = KB_DIR / src["vendor_to"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(RAW_DIR / docs[0].raw_file, dst)
        status = status_for(src, docs, chunks, eff_kind, notes, args)
        stale = CHUNKS_DIR / f"{sid}.jsonl"
        if chunks:
            write_chunks(sid, chunks)
        elif stale.exists():
            stale.unlink()
        results[sid] = {"status": status, "chunks": len(chunks), "kind": eff_kind, "binding": bool(src.get("binding")),
                        "raw_file": docs[0].raw_file if docs else None, "notes": notes, "declared": src.get("status"),
                        "pages": max([d.page_starts[-1][1] for d in docs if d.page_starts] or [0]),
                        "extraction": sorted({d.method for d in docs})}
        log(f"    -> {status}, {len(chunks)} chunks" + (f" ({'; '.join(notes)})" if notes else ""))
        save_manifest(manifest)

    anchors, _ = collect_anchors()
    # coverage (sources not processed in this run keep their previous actual status)
    all_status = {}
    prev_status = {}
    if (CHUNKS_DIR / "_status.json").exists():
        try:
            prev_status = json.loads((CHUNKS_DIR / "_status.json").read_text(encoding="utf-8")).get("sources", {})
        except json.JSONDecodeError:
            prev_status = {}
    for src in sources:
        sid = src["id"]
        if sid in results:
            all_status[sid] = results[sid]
        elif sid in prev_status:
            all_status[sid] = prev_status[sid]
        else:
            n = sum(1 for _ in open(CHUNKS_DIR / f"{sid}.jsonl", encoding="utf-8")) if (CHUNKS_DIR / f"{sid}.jsonl").exists() else 0
            all_status[sid] = {"status": src.get("status", "unknown"), "chunks": n, "kind": src.get("kind", "primary"), "binding": bool(src.get("binding")),
                               "raw_file": src.get("raw_file"), "notes": [], "pages": 0, "extraction": []}
    declared_anchor_names = {f"{s['id']}#{n}" for s in sources for n in (s.get("anchors") or {})}
    unresolved = sorted(declared_anchor_names - set(anchors))
    print_coverage(sources, all_status, anchors, unresolved)
    (CHUNKS_DIR / "_status.json").write_text(json.dumps({"sources": all_status, "anchors_resolved": len(anchors), "anchors_unresolved": unresolved},
                                                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_coverage_md(sources, all_status, anchors, unresolved)
    try:  # refresh the gap register (seed + runtime-free export) so its anchors are always checked against the new chunks
        import importlib.util
        spec = importlib.util.spec_from_file_location("export_gaps", KB_DIR / "gaps" / "export_gaps.py")
        if spec and (KB_DIR / "gaps" / "seed_gaps.yaml").exists():
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            reg = mod.export()
            log(f"gap register: {len(reg['gaps'])} gaps, unresolved anchors: {reg['unresolved_anchors'] or 'none'}")
    except Exception as e:  # never break the KB build because of the gap export
        log(f"gap export skipped: {type(e).__name__}: {e}")
    if args.sync_status:
        sync_status(KB_DIR / "sources.yaml", all_status)
        log("sources.yaml status synchronised")
    if args.load_db:
        try:
            st = load_to_db(embed=not args.no_embed)
            log(f"Loaded into Postgres: {st}")
        except Exception as e:
            log(f"DB load failed: {type(e).__name__}: {e}")
            return 2
    return 0


def coverage_rows(sources, st):
    rows = []
    for s in sources:
        r = st[s["id"]]
        rows.append((s["id"], r["kind"], "yes" if r["binding"] else "no", r["status"], r["chunks"], "/".join(r.get("extraction") or []) or "-"))
    return rows


def print_coverage(sources, st, anchors, unresolved):
    rows = coverage_rows(sources, st)
    w = max(len(r[0]) for r in rows)
    print("\n" + "=" * 100)
    print(f"{'source':<{w}}  {'kind':<8} {'binding':<8} {'status':<17} {'chunks':>6}  extraction")
    print("-" * 100)
    for r in rows:
        print(f"{r[0]:<{w}}  {r[1]:<8} {r[2]:<8} {r[3]:<17} {r[4]:>6}  {r[5]}")
    tot = sum(r[4] for r in rows)
    ok = sum(1 for r in rows if r[4] > 0)
    print("-" * 100)
    print(f"{len(rows)} sources, {ok} with chunks, {tot} chunks, {len(anchors)} anchors resolved"
          + (f", UNRESOLVED anchors: {', '.join(unresolved)}" if unresolved else ""))
    pend = [r[0] for r in rows if r[3] in ("pending_download", "pending_ocr")]
    if pend:
        print("Needs manual drop-in in kb/raw/<source_id>.<pdf|html>: " + ", ".join(pend))
    print("=" * 100)


def write_coverage_md(sources, st, anchors, unresolved):
    rows = coverage_rows(sources, st)
    L = ["# KB coverage (generated by kb/build_kb.py — do not edit)", "",
         "| source | kind | binding | status | chunks | extraction | note |", "|---|---|---|---|---:|---|---|"]
    for s, r in zip(sources, rows):
        note = (s.get("notes") or "").replace("|", "/").replace("\n", " ")[:160] if r[3] != "downloaded" else ""
        L.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {note} |")
    L += ["", f"Total: {len(rows)} sources, {sum(1 for r in rows if r[4] > 0)} with chunks, {sum(r[4] for r in rows)} chunks, {len(anchors)} anchors.",
          ("Unresolved anchors (no real chunk supports them): " + ", ".join(unresolved)) if unresolved else "All declared anchors resolved."]
    (KB_DIR / "COVERAGE.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
