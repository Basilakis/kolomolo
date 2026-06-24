"""
Value/unit verification guard — the DETECT layer of hallucination assurance.

After the LLM writes its answer, we independently check that every *numeric claim with a unit*
in the prose corresponds to a value actually present in the graph evidence. A number+unit that
isn't backed by the evidence is flagged as a potential hallucination.

We scope the check to number+unit patterns (e.g. "4-20 cmH2O", "1.33 kg") on purpose: it ignores
model-name digits ("AirSense 11") and citation page numbers ("p.5"), which are not measurements.
"""
from __future__ import annotations

import re

# Unit tokens we treat as a measurement marker (mirror ontology/units.py spellings).
_UNIT = r"cm\s?h2o|cmh2o|cm\s?h₂o|hpa|mbar|kg|lbs?|min(?:utes)?|db\(a\)|dba|db|watts?|w|l/min"
_NUM_UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(" + _UNIT + r")", re.IGNORECASE)


def _round(x) -> float | None:
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _evidence_numbers(result: dict) -> set[float]:
    nums: set[float] = set()

    def add(x):
        v = _round(x)
        if v is not None:
            nums.add(v)

    for row in result.get("rows", []):
        for k in ("value", "min", "max", "default"):
            add(row.get(k))
    for cand in result.get("ranking", []):
        for p in cand.get("evidence", {}).get("parameters", []):
            for k in ("value", "min", "max"):
                add(p.get(k))
    return nums


def verify_numeric_grounding(answer_text: str, result: dict) -> dict:
    """
    Returns {grounded: bool, unverified_claims: [{value, unit}], checked: int}.
    grounded=True means every number+unit in the answer is present in the evidence.
    """
    evidence = _evidence_numbers(result)
    unverified, checked = [], 0
    for m in _NUM_UNIT_RE.finditer(answer_text):
        checked += 1
        val = _round(m.group(1).replace(",", "."))
        if val is None:
            continue
        if val not in evidence:
            unverified.append({"value": m.group(1), "unit": m.group(2)})
    return {"grounded": not unverified, "unverified_claims": unverified, "checked": checked}
