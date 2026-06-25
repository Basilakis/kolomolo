"""
Plan -> graph execution. Routes each QueryType to its Cypher template and returns
raw rows (always provenance-bearing) plus, for recommendation, a transparent ranking.
"""
from __future__ import annotations

from ..graph import queries as Q
from ..graph.client import GraphClient
from ..ontology import units
from .planner import Plan, QueryType


def _canonicalize(constraints) -> list[dict]:
    out = []
    for c in constraints:
        cval = units.to_canonical(c.value, c.unit)
        if cval is None:
            cval = c.value  # fall back to raw if unit unknown (logged upstream)
        out.append({"parameter": c.parameter, "op": c.op, "canonical_value": cval})
    return out


def score_candidates(evidence_rows: list[dict], plan: Plan) -> list[dict]:
    """
    Transparent recommendation ranking (#7). Heuristic + auditable:
      +1 per satisfied hard constraint, + feature matches, mode support.
    Returns rows sorted by score with the evidence that produced each point.
    Tune weights in SOLUTION.md; the point is transparency, not a black box.
    """
    canon = _canonicalize(plan.constraints)
    # Transparent weights — exposed so SOLUTION.md can justify them; not a black box.
    W_CONSTRAINT = 1.0     # satisfies a hard numeric limit (e.g. pressure up to 18)
    W_FEATURE = 1.0        # has a desired/lifestyle feature (e.g. humidification, travel-friendly)
    ranked = []
    for row in evidence_rows:
        score, reasons = 0.0, []
        params = {p["parameter"]: p for p in row.get("parameters", []) if p.get("parameter")}
        # (a) numeric constraint satisfaction
        for c in canon:
            p = params.get(c["parameter"])
            if not p:
                continue
            mag = next((p.get(k) for k in ("cmax", "cmin", "cval") if p.get(k) is not None), None)
            if mag is None:
                continue
            ok = {
                ">": mag > c["canonical_value"], ">=": mag >= c["canonical_value"],
                "<": mag < c["canonical_value"], "<=": mag <= c["canonical_value"],
                "=": mag == c["canonical_value"],
            }[c["op"]]
            if ok:
                score += W_CONSTRAINT
                reasons.append({"type": "constraint", "parameter": c["parameter"],
                                "doc": p.get("doc"), "page": p.get("page")})
        # (b) desired/lifestyle features (travel_friendly, integrated_humidification, …)
        feats = {f["feature"]: f for f in row.get("features", []) if f.get("feature")}
        for want in plan.desired_features:
            f = feats.get(want)
            if f and (f.get("supported") is None or f.get("supported")):
                score += W_FEATURE
                reasons.append({"type": "feature", "feature": want,
                                "doc": f.get("doc"), "page": f.get("page")})
        ranked.append({"device": row["device"], "score": score, "reasons": reasons,
                       "evidence": row})
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked


def execute(plan: Plan, g: GraphClient) -> dict:
    """Run the plan against the graph. Returns {rows | ranking, query_type}."""
    qt = plan.query_type
    device = plan.devices[0] if plan.devices else ""

    if qt in (QueryType.SPEC_LOOKUP, QueryType.PARAM_DETAIL):
        param = plan.parameters[0] if plan.parameters else None
        return {"query_type": qt.value, "rows": Q.spec_lookup(g, device, param)}

    if qt == QueryType.FEATURE_LOOKUP:
        return {"query_type": qt.value, "rows": Q.feature_lookup(g, device, plan.feature)}

    if qt == QueryType.COMPARISON:
        params = plan.parameters or None
        return {"query_type": qt.value, "rows": Q.compare(g, plan.devices, params)}

    if qt == QueryType.MODE_INDICATION:
        return {"query_type": qt.value, "rows": Q.devices_by_mode(g, plan.mode or "")}

    if qt == QueryType.MULTI_CONSTRAINT:
        canon = _canonicalize(plan.constraints)
        names = [r["device"] for r in Q.multi_constraint(g, canon, features=plan.features or None)]
        # enrich matches with the satisfying parameter + provenance so the answer can cite
        param_keys = {c["parameter"] for c in canon}
        rows = []
        for cand in (Q.candidate_evidence(g, names) if names else []):
            for p in cand.get("parameters", []):
                if p.get("parameter") in param_keys and p.get("doc"):
                    rows.append({"device": cand["device"], "parameter": p["parameter"],
                                 "min": p.get("min"), "max": p.get("max"), "value": p.get("value"),
                                 "unit": p.get("unit"), "source_doc": p.get("doc"), "page": p.get("page")})
        return {"query_type": qt.value, "rows": rows or [{"device": n} for n in names]}

    if qt == QueryType.RECOMMENDATION:
        # 1) narrow candidates by HARD constraints (numeric + required features),
        # 2) pull per-candidate evidence, 3) rank by constraints + DESIRED features.
        canon = _canonicalize(plan.constraints)
        if canon or plan.features:
            candidates = [r["device"] for r in
                          Q.multi_constraint(g, canon, features=plan.features or None)]
        else:
            candidates = [r["device"] for r in
                          g.run("MATCH (d:Device) RETURN d.canonical_name AS device")]
        evidence = Q.candidate_evidence(g, candidates) if candidates else []
        return {"query_type": qt.value, "ranking": score_candidates(evidence, plan)}

    return {"query_type": qt.value, "rows": []}
