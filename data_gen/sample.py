"""Small committed sample of the generated data (data/sample/)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

SAMPLE_CASES = ["GEN-001", "TMEN-001", "OCRH-001"]


def write_sample(out: Path, sample_dir: Path, gt: dict, manifest: dict, spec_by_key: dict) -> None:
    if sample_dir.exists():
        shutil.rmtree(sample_dir)
    for sub in ("pv", "diplomas", "registries"):
        (sample_dir / sub).mkdir(parents=True)
    pv_files, reg_files = set(), set()
    for cid in SAMPLE_CASES:
        for ext in ("png", "json"):
            shutil.copy2(out / "diplomas" / f"{cid}.{ext}", sample_dir / "diplomas" / f"{cid}.{ext}")
        ev = gt[cid]["evidence"]
        if ev:
            pv_files.add(Path(ev["pv_file"]).stem)
            reg_files.add(ev["registry_file"])
    # one multi-page PV to exercise page/row/bbox extraction
    multi = sorted(k for k, s in spec_by_key.items() if len(s["rows"]) > 25)
    if multi:
        pv_files.add(multi[0])
    for stem in sorted(pv_files):
        for suffix in (".pdf", ".clean.pdf"):
            shutil.copy2(out / "pv" / f"{stem}{suffix}", sample_dir / "pv" / f"{stem}{suffix}")
    for rf in sorted(reg_files):
        shutil.copy2(out / rf, sample_dir / rf)
    shutil.copy2(out / "institutions.json", sample_dir / "institutions.json")
    idx = json.loads((out / "pv_index.json").read_text(encoding="utf-8"))
    (sample_dir / "pv_index.json").write_text(json.dumps({k: idx[k] for k in sorted(pv_files)}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (sample_dir / "ground_truth.json").write_text(json.dumps({c: gt[c] for c in SAMPLE_CASES}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (sample_dir / "README.md").write_text(
        "# Sample of the synthetic data (SPECIMEN – SYNTHETIC DATA)\n\n"
        "Committed excerpt of `data/synthetic/` (regenerate everything with `python -m data_gen`). "
        "All institutions and people are fictional.\n\n"
        f"- diplomas: {', '.join(SAMPLE_CASES)} (genuine / tampered mention / OCR-hard name)\n"
        f"- pv: {', '.join(sorted(pv_files))} (scanned image-only PDF + `.clean.pdf` with text layer, tests only)\n"
        "- registries: the registry files covering those sessions\n", encoding="utf-8")
