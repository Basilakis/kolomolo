"""
Query planner — classifies a question into one of the seven target query types and
extracts its parameters (device names, parameter/feature/mode, numeric constraints).

We use an LLM with a forced schema for robust slot-filling, but the ROUTING is then
deterministic: each query type maps to a fixed Cypher template. This is the
"agent planning shaped around the seven categories" the rubric asks for.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ..config import settings


class QueryType(str, Enum):
    SPEC_LOOKUP = "spec_lookup"                 # #1
    PARAM_DETAIL = "param_detail"               # #2 unit/range/default
    FEATURE_LOOKUP = "feature_lookup"           # #3
    COMPARISON = "comparison"                    # #4
    MODE_INDICATION = "mode_indication"         # #5
    MULTI_CONSTRAINT = "multi_constraint"       # #6
    RECOMMENDATION = "recommendation"           # #7
    UNSUPPORTED = "unsupported"                  # refuse / out of scope


class Constraint(BaseModel):
    parameter: str               # controlled ParameterName
    op: str                      # one of > >= < <= =
    value: float                 # in the unit named below (planner reports raw; we canonicalize)
    unit: str


class Plan(BaseModel):
    query_type: QueryType
    devices: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)   # controlled ParameterName values
    feature: Optional[str] = None                         # single-feature lookup (#3)
    features: list[str] = Field(default_factory=list)     # controlled FeatureName keys, hard filter (#6)
    mode: Optional[str] = None
    constraints: list[Constraint] = Field(default_factory=list)
    # for recommendation (#7): soft/desired features that score but don't exclude
    desired_features: list[str] = Field(default_factory=list)
    patient_context: Optional[str] = None


_PLANNER_TOOL = {
    "name": "emit_plan",
    "description": "Classify the user's CPAP question into one of the seven query types and "
                   "fill the slots needed to run it. Use controlled parameter names where possible "
                   "(pressure_range, pressure_max, ramp_time, weight, noise_level, ...).",
    "input_schema": {
        "type": "object",
        "properties": {
            "query_type": {"type": "string",
                           "enum": [t.value for t in QueryType]},
            "devices": {"type": "array", "items": {"type": "string"}},
            "parameters": {"type": "array", "items": {"type": "string"}},
            "feature": {"type": ["string", "null"],
                        "description": "single controlled feature key for a #3 lookup"},
            "features": {"type": "array", "items": {"type": "string"},
                         "description": "controlled feature keys that are HARD requirements (#6), "
                                        "e.g. cellular_connectivity, integrated_humidification"},
            "desired_features": {"type": "array", "items": {"type": "string"},
                                 "description": "controlled feature keys that are DESIRED for a "
                                                "#7 recommendation (score but do not exclude)"},
            "mode": {"type": ["string", "null"]},
            "constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "parameter": {"type": "string"},
                        "op": {"type": "string", "enum": [">", ">=", "<", "<=", "="]},
                        "value": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                    "required": ["parameter", "op", "value", "unit"],
                },
            },
            "patient_context": {"type": ["string", "null"]},
        },
        "required": ["query_type"],
    },
}

from ..ontology.features import FeatureName  # noqa: E402

_FEATURE_KEYS = ", ".join(f.value for f in FeatureName if f != FeatureName.OTHER)

_SYSTEM = (
    "You are a query planner for a CPAP knowledge graph. Map the question to exactly one of the "
    "seven query types and extract its slots. Do not answer the question. If it is not about CPAP "
    "device specs/features/modes/recommendation, return query_type=unsupported.\n\n"
    "Use these CONTROLLED feature keys for `feature`/`features`/`desired_features` "
    f"(pick the closest, else omit): {_FEATURE_KEYS}.\n"
    "For constraint questions (#6) put hard feature requirements in `features` and numeric "
    "limits in `constraints`. For recommendations (#7) put hard limits in `constraints` and "
    "nice-to-haves in `desired_features` (e.g. 'frequent travel' -> travel_friendly; "
    "'with humidification' -> integrated_humidification)."
)


def plan(question: str) -> tuple[Plan, float]:
    """Return (Plan, cost_usd). Cost is surfaced so the eval harness can total it."""
    from ..llm import call
    res = call(
        settings.agent_model,
        max_tokens=1024,
        system=_SYSTEM,
        tools=[_PLANNER_TOOL],
        tool_choice={"type": "tool", "name": "emit_plan"},
        messages=[{"role": "user", "content": question}],
    )
    for block in res.message.content:
        if block.type == "tool_use" and block.name == "emit_plan":
            return Plan.model_validate(block.input), res.cost_usd
    return Plan(query_type=QueryType.UNSUPPORTED), res.cost_usd
