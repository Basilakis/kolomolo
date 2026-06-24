"""
Segmentation — table-aware.

Spec tables are kept intact as single segments (splitting them destroys the
value<->parameter<->unit association). Prose is chunked to a target size. Every
segment keeps its (source_doc, page) so downstream extraction stays grounded.
"""
from __future__ import annotations

from dataclasses import dataclass

from .parse import Page

TARGET_CHARS = 2500
OVERLAP_CHARS = 200


@dataclass
class Segment:
    source_doc: str
    page: int
    kind: str          # "table" | "prose"
    content: str


def _table_to_text(grid: list[list[str]]) -> str:
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in grid)


def segment_pages(pages: list[Page]) -> list[Segment]:
    segs: list[Segment] = []
    for pg in pages:
        # 1) each table is its own segment (high-value for numeric extraction)
        for grid in pg.tables:
            segs.append(Segment(pg.source_doc, pg.page, "table", _table_to_text(grid)))
        # 2) prose chunked with light overlap
        text = pg.text or ""
        start = 0
        while start < len(text):
            chunk = text[start:start + TARGET_CHARS]
            if chunk.strip():
                segs.append(Segment(pg.source_doc, pg.page, "prose", chunk))
            start += TARGET_CHARS - OVERLAP_CHARS
    return segs
