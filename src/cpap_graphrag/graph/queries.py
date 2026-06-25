"""
Query-type-tuned Cypher templates — the concrete evidence of "optimised for the
seven target query types". The agent planner (agent/planner.py) classifies a question
into one of these and calls the matching function. Each returns rows that ALWAYS carry
provenance (doc + page) so the answer layer can cite.

Templates > free-form text-to-Cypher: deterministic, auditable, harder to hallucinate.
"""
from __future__ import annotations

from typing import Optional

from .client import GraphClient

# --- #1 Specification lookup ------------------------------------------------- #
Q_SPEC_LOOKUP = """
MATCH (d:Device)-[:HAS_PARAMETER]->(q:Quantity)-[s:SOURCED_FROM]->(doc:Document)
WHERE all(tok IN [t IN split(toLower($device),' ') WHERE size(t)>1]
          WHERE toLower(d.canonical_name) CONTAINS tok)
  AND ($parameter IS NULL OR q.parameter = $parameter)
RETURN d.canonical_name AS device, q.parameter AS parameter, q.raw_label AS label,
       q.value AS value, q.min AS min, q.max AS max, q.default AS default,
       q.raw_unit AS unit, q.unit_ucum AS ucum,
       doc.name AS source_doc, s.page AS page, s.snippet AS snippet
"""

# --- #2 Parameter / unit / range / default ----------------------------------- #
# Same node, but we surface default + range explicitly (one node answers it).
Q_PARAM_DETAIL = Q_SPEC_LOOKUP  # parameter filter makes it specific; reuse.

# --- #3 Feature lookup ------------------------------------------------------- #
# Filter on the controlled feature key first (e.g. 'integrated_humidification'),
# falling back to a text match on name/label for free-text queries.
Q_FEATURE_LOOKUP = """
MATCH (d:Device)-[r:HAS_FEATURE]->(f:Feature)-[s:SOURCED_FROM]->(doc:Document)
WHERE all(tok IN [t IN split(toLower($device),' ') WHERE size(t)>1]
          WHERE toLower(d.canonical_name) CONTAINS tok)
  AND ($feature IS NULL OR f.key = $feature
       OR toLower(f.name) CONTAINS toLower($feature)
       OR toLower(f.raw_label) CONTAINS toLower($feature))
RETURN d.canonical_name AS device, f.key AS feature_key, f.name AS feature,
       f.raw_label AS label, r.supported AS supported, f.detail AS detail,
       doc.name AS source_doc, s.page AS page
"""

# --- #4 Differentiator / comparison ------------------------------------------ #
# Join N devices on a shared parameter so the answer can table them side by side.
Q_COMPARE_PARAM = """
MATCH (d:Device)-[:HAS_PARAMETER]->(q:Quantity)-[s:SOURCED_FROM]->(doc:Document)
WHERE any(name IN $devices WHERE all(tok IN [t IN split(toLower(name),' ') WHERE size(t)>1]
                                     WHERE toLower(d.canonical_name) CONTAINS tok))
  AND ($parameters IS NULL OR q.parameter IN $parameters)
RETURN d.canonical_name AS device, q.parameter AS parameter,
       q.value AS value, q.min AS min, q.max AS max, q.default AS default,
       q.raw_unit AS unit, doc.name AS source_doc, s.page AS page
ORDER BY parameter, device
"""

# --- #5 Mode & indication ---------------------------------------------------- #
Q_DEVICES_BY_MODE = """
MATCH (d:Device)-[:SUPPORTS_MODE]->(m:Mode)-[s:SOURCED_FROM]->(doc:Document)
WHERE toLower(m.name) CONTAINS toLower($mode)
RETURN d.canonical_name AS device, m.name AS mode,
       doc.name AS source_doc, s.page AS page
ORDER BY device
"""

# --- #6 Multi-device constraint (numeric AND feature) ------------------------ #
# Filters on canonical (SI) magnitudes for numeric compare AND on controlled feature
# keys (e.g. cellular_connectivity) — query #6 mixes both: ">20 cmH2O, <1.5 kg, with
# cellular connectivity". Built dynamically because the WHERE clause is variable.
Q_CONSTRAINT_BASE = """
MATCH (d:Device)
WHERE {conditions}
RETURN DISTINCT d.canonical_name AS device
ORDER BY device
"""
_COND_NUMERIC = """
EXISTS {{
  MATCH (d)-[:HAS_PARAMETER]->(q{i}:Quantity)
  WHERE q{i}.parameter = $param{i}
    AND coalesce(q{i}.canonical_max, q{i}.canonical_value) {op} $val{i}
}}
"""
_COND_FEATURE = """
EXISTS {{
  MATCH (d)-[hf{i}:HAS_FEATURE]->(f{i}:Feature {{key: $feat{i}}})
  WHERE coalesce(hf{i}.supported, true) = true
}}
"""

# --- #7 Recommendation: candidate evidence ----------------------------------- #
# Pull all evidence for a candidate set so the scorer can rank with per-candidate proof.
Q_CANDIDATE_EVIDENCE = """
MATCH (d:Device)
WHERE d.canonical_name IN $devices
OPTIONAL MATCH (d)-[:HAS_PARAMETER]->(q:Quantity)-[sp:SOURCED_FROM]->(pd:Document)
OPTIONAL MATCH (d)-[hf:HAS_FEATURE]->(f:Feature)-[sf:SOURCED_FROM]->(fd:Document)
OPTIONAL MATCH (d)-[:SUPPORTS_MODE]->(m:Mode)
RETURN d.canonical_name AS device,
       collect(DISTINCT {parameter:q.parameter, min:q.min, max:q.max, value:q.value,
                         cmin:q.canonical_min, cmax:q.canonical_max, unit:q.raw_unit,
                         doc:pd.name, page:sp.page}) AS parameters,
       collect(DISTINCT {feature:f.name, supported:hf.supported, doc:fd.name, page:sf.page}) AS features,
       collect(DISTINCT m.name) AS modes
"""


# --------------------------------------------------------------------------- #
# Callable helpers
# --------------------------------------------------------------------------- #
def spec_lookup(g: GraphClient, device: str, parameter: Optional[str] = None):
    return g.run(Q_SPEC_LOOKUP, device=device, parameter=parameter)


def feature_lookup(g: GraphClient, device: str, feature: Optional[str] = None):
    return g.run(Q_FEATURE_LOOKUP, device=device, feature=feature)


def compare(g: GraphClient, devices: list[str], parameters: Optional[list[str]] = None):
    return g.run(Q_COMPARE_PARAM, devices=devices, parameters=parameters)


def devices_by_mode(g: GraphClient, mode: str):
    return g.run(Q_DEVICES_BY_MODE, mode=mode)


def multi_constraint(g: GraphClient, constraints: list[dict] | None = None,
                     features: list[str] | None = None):
    """
    Combine numeric AND feature constraints (query #6).

    constraints: [{"parameter": "pressure_max", "op": ">", "canonical_value": 1961.3}, ...]
                 op in >,>=,<,<=,= ; canonical_value in SI base units (see ontology/units).
    features:    ["cellular_connectivity", "integrated_humidification", ...]  (controlled keys)

    A device matches only if it satisfies every numeric condition AND has every feature.
    """
    allowed = {">", ">=", "<", "<=", "="}
    conds, params = [], {}
    for i, c in enumerate(constraints or []):
        op = c["op"]
        if op not in allowed:
            raise ValueError(f"illegal operator {op!r}")
        conds.append(_COND_NUMERIC.format(i=i, op=op))
        params[f"param{i}"] = c["parameter"]
        params[f"val{i}"] = c["canonical_value"]
    for j, feat in enumerate(features or []):
        conds.append(_COND_FEATURE.format(i=j))
        params[f"feat{j}"] = feat
    if not conds:
        return g.run("MATCH (d:Device) RETURN DISTINCT d.canonical_name AS device ORDER BY device")
    cypher = Q_CONSTRAINT_BASE.format(conditions=" AND ".join(conds))
    return g.run(cypher, **params)


def candidate_evidence(g: GraphClient, devices: list[str]):
    return g.run(Q_CANDIDATE_EVIDENCE, devices=devices)


# --- Coverage: what did each source document contribute to the graph? --------- #
Q_DOC_COVERAGE = """
MATCH (doc:Document)
OPTIONAL MATCH (n)-[:SOURCED_FROM]->(doc)
OPTIONAL MATCH (dev:Device)-->(n)
RETURN doc.name AS source_doc,
       count(DISTINCT dev) AS devices,
       count(n) AS facts
ORDER BY source_doc
"""


def document_coverage(g: GraphClient):
    return g.run(Q_DOC_COVERAGE)
