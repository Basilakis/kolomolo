"""
Document inventory — run BEFORE writing extraction logic (assignment recommended approach #2).

Unzips the corpus and produces a structured manifest: file, inferred vendor/model from
filename, doc type (datasheet vs manual heuristic), page count, whether it contains
spec-like tables. Output drives SOLUTION.md §2 and tells us where the numbers live.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import settings

# Filenames identify vendor and model (per assignment). Tune these as the corpus reveals patterns.
_VENDOR_HINTS = ["resmed", "philips", "respironics", "fisher", "paykel", "lowenstein",
                 "breas", "devilbiss", "apex", "bmc", "react", "3b"]
_DATASHEET_HINTS = ["datasheet", "spec", "specs", "techsheet", "brochure"]


@dataclass
class DocEntry:
    file: str
    vendor: str
    model: str
    doc_type: str          # "datasheet" | "manual" | "unknown"
    pages: int
    has_tables: bool
    text_chars: int        # extractable text-layer size
    ocr_needed: bool       # true if the file is image-only (negligible text/page)

# Below this many chars/page a PDF is treated as image-only (OCR deferred — see issue #1).
_OCR_CHARS_PER_PAGE = 50


def unzip_corpus() -> Path:
    """Extract the supplied zip into settings.data_dir (idempotent). Returns the dir."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.data_zip.exists():
        with zipfile.ZipFile(settings.data_zip) as zf:
            zf.extractall(settings.data_dir)
    return settings.data_dir


def _guess_vendor(name: str) -> str:
    low = name.lower()
    for v in _VENDOR_HINTS:
        if v in low:
            return v.capitalize()
    return "unknown"


def _guess_model(name: str) -> str:
    # crude: strip extension/vendor/doc-type tokens, keep the rest
    stem = Path(name).stem
    tokens = re.split(r"[\s_\-]+", stem)
    drop = set(_VENDOR_HINTS) | set(_DATASHEET_HINTS) | {"manual", "user", "guide", "clinical"}
    kept = [t for t in tokens if t.lower() not in drop]
    return " ".join(kept).strip() or stem


def _guess_doc_type(name: str) -> str:
    low = name.lower()
    if any(h in low for h in _DATASHEET_HINTS):
        return "datasheet"
    if "manual" in low or "guide" in low:
        return "manual"
    return "unknown"


def build_inventory() -> list[DocEntry]:
    """Walk the unpacked corpus and return one DocEntry per PDF."""
    import fitz  # PyMuPDF — imported lazily so `inventory` works before all deps installed

    corpus = unzip_corpus()
    entries: list[DocEntry] = []
    for pdf in sorted(corpus.rglob("*.pdf")):
        try:
            doc = fitz.open(pdf)
            pages = doc.page_count
            text_chars = sum(len(p.get_text()) for p in doc)
            # cheap table heuristic: many tab/aligned digits on a page
            has_tables = any("\t" in p.get_text() or p.find_tables().tables for p in doc)
            doc.close()
        except Exception:
            pages, text_chars, has_tables = 0, 0, False
        ocr_needed = pages > 0 and (text_chars / max(pages, 1)) < _OCR_CHARS_PER_PAGE
        entries.append(DocEntry(
            file=pdf.name,
            vendor=_guess_vendor(pdf.name),
            model=_guess_model(pdf.name),
            doc_type=_guess_doc_type(pdf.name),
            pages=pages,
            has_tables=has_tables,
            text_chars=text_chars,
            ocr_needed=ocr_needed,
        ))
    return entries


def inventory_as_dicts() -> list[dict]:
    return [asdict(e) for e in build_inventory()]
