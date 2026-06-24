"""
Evaluation runner — GraphRAG vs vector-only baseline on the same question set.

    python -m cpap_graphrag.eval.run_eval

Produces a side-by-side table + JSON under eval/results/. The headline comparison
(value/unit correctness, citation validity, per-category breakdown) is what justifies
the GraphRAG investment in SOLUTION.md §8.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml
from rich import print
from rich.table import Table

from ..agent.answer import answer_question as graph_answer
from ..baseline.vector_rag import answer_question as vector_answer
from ..config import ROOT
from ..ingestion.inventory import inventory_as_dicts
from . import metrics

QUESTIONS = Path(__file__).parent / "questions.yaml"
RESULTS_DIR = ROOT / "eval" / "results"


def _inventory_pages() -> dict[str, int]:
    return {e["file"]: e["pages"] for e in inventory_as_dicts()}


def _score(result: dict, item: dict, inv: dict) -> dict:
    expected = item.get("expected", {})
    return {
        "id": item["id"], "category": item["category"],
        "value_unit_correct": metrics.value_unit_correct(result.get("answer", ""), expected),
        "citations_resolve": metrics.citations_resolve(result.get("citations", []), inv),
        "has_citations": metrics.has_citations(result),
        "latency_s": result.get("latency_s"),
        "cost_usd": result.get("cost_usd", 0),
    }


def _timed(fn, *a, **k) -> dict:
    t0 = time.perf_counter()
    out = fn(*a, **k)
    out["latency_s"] = round(time.perf_counter() - t0, 3)
    return out


def main() -> None:
    items = yaml.safe_load(QUESTIONS.read_text())
    inv = _inventory_pages()

    graph_rows, vec_rows = [], []
    for item in items:
        q = item["question"]
        print(f"[cyan]Q{item['id']}[/cyan] ({item['category']}) {q}")
        graph_rows.append(_score(_timed(graph_answer, q), item, inv))
        vec_rows.append(_score(_timed(vector_answer, q), item, inv))

    g, v = metrics.summarize(graph_rows), metrics.summarize(vec_rows)

    table = Table(title="GraphRAG vs Vector-only baseline")
    table.add_column("metric"); table.add_column("GraphRAG"); table.add_column("Vector baseline")
    for key in ("value_unit_correctness", "citation_validity", "has_citations", "latency_p50_s", "total_cost_usd"):
        table.add_row(key, str(g.get(key)), str(v.get(key)))
    print(table)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "comparison.json").write_text(json.dumps(
        {"graphrag": {"summary": g, "rows": graph_rows},
         "baseline": {"summary": v, "rows": vec_rows}}, indent=2))
    print(f"[green]Wrote {RESULTS_DIR / 'comparison.json'}[/green]")


if __name__ == "__main__":
    main()
