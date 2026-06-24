"""
PDF parsing with per-page provenance.

Returns a list of Page records (text + tables + page number). Tables matter most:
CPAP specs live in spec tables, and we must keep them intact for accurate numeric
extraction. Page numbers are carried through everything for audit-grade citation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Page:
    source_doc: str
    page: int                 # 1-based
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)  # list of grids


def parse_pdf(path: Path) -> list[Page]:
    """Extract text + tables page by page using PyMuPDF (text) and pdfplumber (tables)."""
    import fitz          # PyMuPDF
    import pdfplumber

    pages: list[Page] = []
    doc = fitz.open(path)
    texts = [p.get_text("text") for p in doc]
    doc.close()

    with pdfplumber.open(path) as pdf:
        for i, pl_page in enumerate(pdf.pages):
            tables = []
            try:
                for t in pl_page.extract_tables() or []:
                    # normalize None cells -> ""
                    tables.append([[c or "" for c in row] for row in t])
            except Exception:
                pass
            page_text = texts[i] if i < len(texts) else (pl_page.extract_text() or "")
            # Image-only page: negligible text and no tables -> log, do not OCR (deferred, #1).
            if len(page_text.strip()) < 20 and not tables:
                print(f"[parse] {path.name} p.{i + 1}: image-only page, skipped (OCR deferred, #1)")
            pages.append(Page(
                source_doc=path.name,
                page=i + 1,
                text=page_text,
                tables=tables,
            ))
    return pages
