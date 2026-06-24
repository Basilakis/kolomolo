"""
Ingestion CLI — reproducible, processes EVERY file in the archive.

    python -m cpap_graphrag.ingestion.cli inventory      # manifest only (no LLM, fast)
    python -m cpap_graphrag.ingestion.cli ingest         # full pipeline
    python -m cpap_graphrag.ingestion.cli ingest --limit 2   # smoke test on N docs
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print
from rich.table import Table

from ..config import settings
from ..graph.client import GraphClient
from . import inventory as inv
from .extract import extract_segments
from .load import load_records
from .parse import parse_pdf
from .resolve import resolve
from .segment import segment_pages
from .state import already_ingested, ensure_state_index, file_sha256, mark_ingested

app = typer.Typer(add_completion=False, help="CPAP corpus ingestion")


@app.command()
def inventory(out: Path = typer.Option(Path("data/inventory.json"), help="where to write the manifest")):
    """Unzip + inventory the corpus. Run this FIRST (before extraction code)."""
    entries = inv.inventory_as_dicts()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2))
    table = Table(title=f"Document inventory ({len(entries)} files)")
    for col in ("file", "vendor", "model", "doc_type", "pages", "tables", "ocr_needed"):
        table.add_column(col)
    for e in entries:
        table.add_row(e["file"], e["vendor"], e["model"], e["doc_type"], str(e["pages"]),
                      str(e["has_tables"]), str(e["ocr_needed"]))
    print(table)
    print(f"[green]Wrote {out}[/green]")


@app.command()
def dedup():
    """Merge duplicate Device nodes graph-wide by normalized vendor/model key (idempotent)."""
    from .dedup import dedup_graph
    removed = dedup_graph(GraphClient())
    print(f"[green]Dedup complete: merged {removed} duplicate device node(s).[/green]")


@app.command()
def coverage(out: Path = typer.Option(Path("data/coverage.json"), help="where to write the report")):
    """Reconcile every corpus file against what it contributed to the graph.

    Proves 'full corpus coverage': each file is shown as device-yielding, image-only
    (OCR deferred, #1), or reference/non-spec (ingested but no device specs — by design).
    """
    from ..graph import queries as gq

    inv_map = {e["file"]: e for e in inv.inventory_as_dicts()}
    g = GraphClient()
    cov = {r["source_doc"]: r for r in gq.document_coverage(g)}

    rows = []
    for fname, e in inv_map.items():
        c = cov.get(fname, {})
        devices = c.get("devices", 0)
        if e["ocr_needed"]:
            status = "image-only (OCR deferred #1)"
        elif devices == 0:
            status = "reference/non-spec (no device specs)"
        else:
            status = "device specs"
        rows.append({"file": fname, "pages": e["pages"], "ocr_needed": e["ocr_needed"],
                     "devices": devices, "facts": c.get("facts", 0), "status": status})

    table = Table(title=f"Corpus coverage ({len(rows)} files)")
    for col in ("file", "pages", "devices", "facts", "status"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["file"], str(r["pages"]), str(r["devices"]), str(r["facts"]), r["status"])
    print(table)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    print(f"[green]Wrote {out}[/green]")


@app.command()
def ingest(
    limit: int = typer.Option(0, help="process only the first N PDFs (0 = all)"),
    workers: int = typer.Option(8, help="parallel LLM extraction workers (rate-limit bound)"),
    force: bool = typer.Option(False, help="re-ingest files even if their content hash is unchanged"),
):
    """Full pipeline: parse -> segment -> extract -> resolve -> load into Neo4j.

    Idempotent: files whose content hash is already recorded are skipped unless --force.
    """
    corpus = inv.unzip_corpus()
    pdfs = sorted(corpus.rglob("*.pdf"))
    if limit:
        pdfs = pdfs[:limit]
    if not pdfs:
        print("[red]No PDFs found. Put the zip in data/ first.[/red]")
        raise typer.Exit(1)

    g = GraphClient()
    g.ensure_schema()
    ensure_state_index(g)

    all_records = []
    processed: list[tuple[str, str]] = []   # (file name, content hash) to mark on success
    for pdf in pdfs:
        digest = file_sha256(pdf)
        if not force and already_ingested(g, digest):
            print(f"[yellow]skip[/yellow] {pdf.name} (already ingested, unchanged)")
            continue
        print(f"[cyan]parse[/cyan] {pdf.name}")
        pages = parse_pdf(pdf)
        segs = segment_pages(pages)
        print(f"  segments: {len(segs)}  -> extracting (workers={workers})…")
        records = extract_segments(segs, max_workers=workers)
        print(f"  device-records: {len(records)}")
        all_records.extend(records)
        processed.append((pdf.name, digest))

    if not processed:
        print("[green]Nothing new to ingest.[/green]")
        return

    print(f"[cyan]resolve[/cyan] {len(all_records)} raw records")
    resolved = resolve(all_records)
    print(f"  resolved devices: {len(resolved)}")

    print("[cyan]load[/cyan] -> Neo4j")
    n = load_records(resolved, client=g)
    print(f"[green]Loaded {n} devices.[/green]")

    # Self-healing dedup across the WHOLE graph (prevents duplicates accumulating
    # across separate/incremental ingests — issue #7).
    from .dedup import dedup_graph
    removed = dedup_graph(g)
    if removed:
        print(f"[green]Dedup: merged {removed} duplicate device node(s).[/green]")

    # Mark only after a successful load, so a crash mid-run leaves files un-marked
    # (they will be re-processed on the next run — crash-safe by construction).
    for name, digest in processed:
        mark_ingested(g, name, digest)
    print(f"[green]Marked {len(processed)} source files ingested.[/green]")


if __name__ == "__main__":
    app()
