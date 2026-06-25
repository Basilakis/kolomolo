"""
Evaluation metrics — candidate-proposed, justified in SOLUTION.md §7.

Primary: value/unit correctness (the core business value, "units are not free text").
Also: citation validity (do cited doc+page resolve), retrieval coverage, latency, cost.

These operate on the unified answer dict shape:
  {answer, citations:[{source_doc,page}], subgraph/rows, query_type, latency_s, cost_usd}
"""
from __future__ import annotations

import re
from typing import Optional


def value_unit_correct(answer_text: str, expected: dict) -> Optional[bool]:
    """
    Did the answer state the expected numeric value(s) AND unit?
    Returns True/False, or None if the gold value isn't filled in yet.
    Looks for each expected number as a token and the unit substring.
    """
    targets = [expected.get(k) for k in ("value", "min", "max", "default")]
    targets = [t for t in targets if t is not None]
    unit = expected.get("unit")
    if not targets and unit is None:
        return None  # gold not filled
    text = answer_text.lower().replace("₂", "2")
    nums_ok = all(re.search(rf"(?<!\d){re.escape(str(t))}(?!\d)", text) for t in targets)
    # compare units ignoring spaces ("cmH2O" == "cm H2O") and case
    norm = lambda s: s.lower().replace("₂", "2").replace(" ", "")
    unit_ok = (unit is None) or (norm(unit) in norm(answer_text))
    return bool(nums_ok and unit_ok)


def citations_resolve(citations: list[dict], inventory: dict[str, int]) -> bool:
    """Every cited (doc, page) exists and page <= that doc's page count."""
    if not citations:
        return False
    for c in citations:
        doc, page = c.get("source_doc"), c.get("page")
        if doc not in inventory or page is None or page < 1 or page > inventory[doc]:
            return False
    return True


def has_citations(result: dict) -> bool:
    return bool(result.get("citations"))


def refused_appropriately(result: dict, should_answer: bool) -> bool:
    """True if refusal behaviour matches whether the corpus can answer it."""
    return result.get("refused", False) != should_answer


def summarize(rows: list[dict]) -> dict:
    """Aggregate a list of per-question metric dicts into headline numbers."""
    def rate(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None
    lat = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    return {
        "n": len(rows),
        "value_unit_correctness": rate("value_unit_correct"),
        "citation_validity": rate("citations_resolve"),
        "has_citations": rate("has_citations"),
        "latency_p50_s": round(sorted(lat)[len(lat) // 2], 3) if lat else None,
        "total_cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 4),
    }
