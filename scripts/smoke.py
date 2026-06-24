"""
End-to-end smoke test: one question per target category through the live GraphRAG agent.

Asserts that each answer either (a) is a legitimate refusal, or (b) carries at least one
citation whose (source_doc, page) resolves against the inventory. Exits non-zero on failure.

Run via scripts/smoke.sh (which ensures Neo4j is up and ingests one document first), or
directly once the graph is populated and ANTHROPIC_API_KEY is set:

    python scripts/smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cpap_graphrag.agent.answer import answer_question          # noqa: E402
from cpap_graphrag.eval import metrics                          # noqa: E402
from cpap_graphrag.ingestion.inventory import inventory_as_dicts  # noqa: E402

# One representative question per of the seven target categories.
QUESTIONS = [
    (1, "What is the pressure range of the AirSense 11?"),
    (2, "What is the default ramp time and configurable range for the DreamStation 2?"),
    (3, "Does the AirCurve 10 VAuto support integrated humidification?"),
    (4, "How do the AirSense 11 and DreamStation 2 differ in humidification, connectivity and noise?"),
    (5, "Which devices support BiPAP ST?"),
    (6, "Which devices deliver more than 20 cmH2O, weigh under 1.5 kg and have cellular connectivity?"),
    (7, "Recommend a CPAP for severe OSA up to 18 cmH2O, frequent travel, with humidification."),
]


def main() -> int:
    inv = {e["file"]: e["pages"] for e in inventory_as_dicts()}
    failures = 0
    print(f"{'cat':>3}  {'status':<10}  {'cites':>5}  detail")
    print("-" * 70)
    for cat, q in QUESTIONS:
        try:
            r = answer_question(q)
        except Exception as exc:
            print(f"{cat:>3}  {'ERROR':<10}  {'-':>5}  {exc}")
            failures += 1
            continue

        cites = r.get("citations", [])
        if r.get("refused"):
            status = "refused"          # acceptable: honest no-evidence behaviour
        elif cites and metrics.citations_resolve(cites, inv):
            status = "ok"
        elif cites:
            status = "BAD-CITE"         # cited a doc/page that doesn't resolve
            failures += 1
        else:
            status = "NO-CITE"          # answered without any citation
            failures += 1
        print(f"{cat:>3}  {status:<10}  {len(cites):>5}  {q[:40]}")

    print("-" * 70)
    if failures:
        print(f"SMOKE FAILED: {failures} issue(s).")
        return 1
    print("SMOKE PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
