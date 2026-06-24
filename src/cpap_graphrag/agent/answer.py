"""
Grounded answer synthesis + citation enforcement + refusal.

The model is given ONLY the graph rows (which all carry doc+page) and must answer
strictly from them, citing each fact. If there are no rows, we refuse rather than
guess — the core hallucination guardrail (SOLUTION.md §9).

Returns: {answer, citations, subgraph, query_type, refused}
"""
from __future__ import annotations

import json
from typing import Any

from ..config import settings
from .planner import Plan, QueryType, plan as make_plan
from .tools import execute
from .verify import verify_numeric_grounding
from ..graph.client import GraphClient


_SYSTEM = (
    "You answer CPAP device questions using ONLY the structured graph evidence provided as JSON. "
    "Rules: (1) Use no outside knowledge. (2) State every numeric value with its unit exactly as given. "
    "(3) Cite each fact as [source_doc p.PAGE]. (4) If the evidence is empty or does not contain the "
    "answer, say you cannot answer from the corpus and stop. Be concise and audit-grade."
)


def _collect_citations(result: dict) -> list[dict]:
    cites: list[dict] = []
    def add(doc, page):
        if doc and page is not None:
            cites.append({"source_doc": doc, "page": page})
    for row in result.get("rows", []):
        add(row.get("source_doc"), row.get("page"))
    for cand in result.get("ranking", []):
        for r in cand.get("reasons", []):
            add(r.get("doc"), r.get("page"))
    # de-dup
    uniq = {(c["source_doc"], c["page"]): c for c in cites}
    return list(uniq.values())


def _has_evidence(result: dict) -> bool:
    return bool(result.get("rows")) or bool(result.get("ranking"))


def answer_question(question: str, g: GraphClient | None = None) -> dict[str, Any]:
    from ..llm import call

    g = g or GraphClient()
    p, plan_cost = make_plan(question)

    if p.query_type == QueryType.UNSUPPORTED:
        return {"answer": "That question is outside the CPAP corpus scope.", "citations": [],
                "subgraph": {}, "query_type": p.query_type.value, "refused": True,
                "plan": p.model_dump(), "cost_usd": plan_cost}

    result = execute(p, g)

    if not _has_evidence(result):
        return {"answer": "I can't answer that from the supplied corpus — no supporting evidence "
                          "was found in the knowledge graph.", "citations": [], "subgraph": result,
                "query_type": result["query_type"], "refused": True, "plan": p.model_dump(),
                "cost_usd": plan_cost}

    citations = _collect_citations(result)

    res = call(
        settings.agent_model,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Question: {question}\n\nGraph evidence (JSON):\n{json.dumps(result, default=str)}",
        }],
    )

    # DETECT: every number+unit in the answer must trace to an evidence value.
    verification = verify_numeric_grounding(res.text, result)
    answer_text = res.text
    if not verification["grounded"]:
        bad = ", ".join(f"{c['value']} {c['unit']}" for c in verification["unverified_claims"])
        answer_text += (f"\n\n> ⚠️ Unverified numeric claim(s) not found in the cited evidence: "
                        f"{bad}. Treat with caution.")

    out = {"answer": answer_text, "citations": citations, "subgraph": result,
           "query_type": result["query_type"], "refused": False, "plan": p.model_dump(),
           "cost_usd": round(plan_cost + res.cost_usd, 6), "verification": verification}
    return out
