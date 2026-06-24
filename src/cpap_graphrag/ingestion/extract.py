"""
LLM extraction — schema-forced so numbers are never free text.

Each segment is sent to Claude with a tool that REQUIRES output matching the
DeviceRecord schema. We extract device identity, parameters (value/min/max/default + unit),
features (boolean/enum), and supported modes — each with verbatim provenance (doc + page +
snippet). The forced schema is itself a primary hallucination guardrail (SOLUTION.md §9).
"""
from __future__ import annotations

import json
from typing import Optional

from ..config import settings
from ..ontology import units
from ..ontology.features import canonical_or_other
from ..ontology.schema import DeviceRecord, PAP_DEVICE_TYPES
from .segment import Segment

# JSON schema handed to the model as a forced tool. Mirrors ontology.schema.DeviceRecord.
_DEVICE_PROPERTIES = {
    "vendor": {"type": "string"},
    "model": {"type": "string"},
    "device_type": {"type": "string", "enum": ["cpap", "apap", "bipap", "other"],
                    "description": "Therapy type. Use 'other' for non-PAP products "
                                   "(masks, humidifiers sold separately, oxygen concentrators, "
                                   "nebulizers, ventilators, accessories) — these are dropped."},
}

EXTRACTION_TOOL = {
    "name": "emit_device_records",
    "description": "Emit structured facts for EVERY distinct PAP device described in the text "
                   "(a page/table may list several). Only include facts explicitly present; "
                   "numbers must carry their unit. Set device_type='other' for non-PAP products.",
    "input_schema": {
        "type": "object",
        "properties": {
            "devices": {"type": "array", "items": {"type": "object", "properties": {
                "vendor": _DEVICE_PROPERTIES["vendor"],
                "model": _DEVICE_PROPERTIES["model"],
                "device_type": _DEVICE_PROPERTIES["device_type"],
                "parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "parameter": {"type": "string",
                                      "description": "one of the controlled ParameterName values or 'other'"},
                        "raw_label": {"type": "string"},
                        "value": {"type": ["number", "null"]},
                        "min": {"type": ["number", "null"]},
                        "max": {"type": ["number", "null"]},
                        "default": {"type": ["number", "null"]},
                        "raw_unit": {"type": "string"},
                        "provenance": {
                            "type": "object",
                            "properties": {
                                "source_doc": {"type": "string"},
                                "page": {"type": "integer"},
                                "snippet": {"type": "string"},
                            },
                            "required": ["source_doc", "page"],
                        },
                    },
                    "required": ["parameter", "raw_label", "raw_unit", "provenance"],
                },
            },
            "features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "raw_label": {"type": "string"},
                        "supported": {"type": ["boolean", "null"]},
                        "detail": {"type": ["string", "null"]},
                        "provenance": {"type": "object"},
                    },
                    "required": ["name", "raw_label", "provenance"],
                },
            },
                "modes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "provenance": {"type": "object"},
                        },
                        "required": ["name", "provenance"],
                    },
                },
            },
            "required": ["vendor", "model", "device_type", "parameters", "features", "modes"]}},
        },
        "required": ["devices"],
    },
}

_SYSTEM = (
    "You are a precise biomedical-device data extractor for CPAP/BiPAP/APAP devices. "
    "Extract ONLY facts explicitly stated in the provided text. Never infer or fill missing "
    "numbers. Always attach the unit exactly as printed. Use the given source_doc and page for "
    "every provenance.\n"
    "A single page or table may describe SEVERAL devices (catalogs/brochures) — emit one entry "
    "per distinct device. Set device_type to cpap/apap/bipap for PAP therapy machines, and "
    "'other' for anything that is NOT a PAP machine (masks, standalone humidifiers, oxygen "
    "concentrators, nebulizers, ventilators, accessories) — those are discarded downstream."
)


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def extract_segment(seg: Segment) -> list[DeviceRecord]:
    """Run forced-schema extraction on one segment. Returns 0..N relevant DeviceRecords."""
    try:
        return _extract_segment_inner(seg)
    except Exception as exc:  # network / rate-limit / transient API error
        # Swallow so one bad segment cannot abort a batch; surfaced for monitoring.
        print(f"[extract] segment {seg.source_doc} p.{seg.page} failed: {exc}")
        return []


def _enrich(rec: DeviceRecord) -> DeviceRecord:
    for q in rec.parameters:
        units.normalize_quantity(q)                       # UCUM/QUDT + canonical magnitudes
    for f in rec.features:
        f.key = canonical_or_other(f.name, f.raw_label, f.detail or "").value  # controlled key
    return rec


def _extract_segment_inner(seg: Segment) -> list[DeviceRecord]:
    client = _client()
    msg = client.messages.create(
        model=settings.extraction_model,
        max_tokens=4096,
        system=_SYSTEM,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "emit_device_records"},
        messages=[{
            "role": "user",
            "content": (
                f"source_doc: {seg.source_doc}\npage: {seg.page}\nkind: {seg.kind}\n\n"
                f"{seg.content}"
            ),
        }],
    )
    out: list[DeviceRecord] = []
    for block in msg.content:
        if block.type == "tool_use" and block.name == "emit_device_records":
            for dev in block.input.get("devices", []):
                try:
                    rec = DeviceRecord.model_validate(dev)
                except Exception:
                    continue
                # RELEVANCE GATE: only PAP therapy devices enter the graph.
                if rec.device_type not in PAP_DEVICE_TYPES:
                    continue
                out.append(_enrich(rec))
    return out


def extract_segments(segs: list[Segment], max_workers: int = 8) -> list[DeviceRecord]:
    """
    Extract over all segments with a BOUNDED thread pool.

    Extraction is the one slow, LLM-bound stage; segments are independent, so we fan
    out across `max_workers` concurrent Claude calls. The cap is deliberate — it keeps
    us inside the API rate limit and bounds memory/cost. This is the right concurrency
    primitive for a fixed-corpus batch; a message queue would be over-engineering here
    (see doc/SCALE_ARCHITECTURE.md for when that changes).
    """
    from concurrent.futures import ThreadPoolExecutor

    out: list[DeviceRecord] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for recs in pool.map(extract_segment, segs):   # each returns a list (0..N devices)
            for rec in recs:
                if rec.parameters or rec.features or rec.modes:
                    out.append(rec)
    return out
