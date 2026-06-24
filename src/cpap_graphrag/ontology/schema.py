"""
Domain schema — the smallest viable ontology shaped around the seven query types.

Mapped to external standards (justified in SOLUTION.md §4):
  - Device identity   -> schema.org/MedicalDevice
  - Quantity kind     -> QUDT (Pressure, Mass, Time, SoundPressureLevel, ...)
  - Unit code         -> UCUM ("cm[H2O]", "kg", "min", "dB", ...)

These Pydantic models are the contract for LLM extraction output AND the shape we
load into Neo4j. Keep them lean; extend only when a query type requires it.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Node labels / relationship types (mirrored in graph/queries.py Cypher)
# --------------------------------------------------------------------------- #
class NodeLabel(str, Enum):
    DEVICE = "Device"
    QUANTITY = "Quantity"        # a measured parameter value/range with a unit
    FEATURE = "Feature"          # boolean / enum capability
    MODE = "Mode"                # therapy mode (CPAP, APAP, BiPAP, BiPAP ST, VAuto, ...)
    VENDOR = "Vendor"
    DOCUMENT = "Document"        # provenance: a source file
    # (page lives on the relationship/quantity for audit-grade citation)


class RelType(str, Enum):
    MADE_BY = "MADE_BY"                 # (Device)-[:MADE_BY]->(Vendor)
    HAS_PARAMETER = "HAS_PARAMETER"     # (Device)-[:HAS_PARAMETER]->(Quantity)
    HAS_FEATURE = "HAS_FEATURE"         # (Device)-[:HAS_FEATURE]->(Feature)
    SUPPORTS_MODE = "SUPPORTS_MODE"     # (Device)-[:SUPPORTS_MODE]->(Mode)
    SOURCED_FROM = "SOURCED_FROM"       # (Quantity|Feature|Mode)-[:SOURCED_FROM {page}]->(Document)


# --------------------------------------------------------------------------- #
# Controlled vocab for parameter names — keeps cross-vendor values comparable.
# Extend as the corpus reveals new parameters; unknown -> "other" + raw label.
# --------------------------------------------------------------------------- #
class ParameterName(str, Enum):
    PRESSURE_RANGE = "pressure_range"          # cmH2O
    PRESSURE_MAX = "pressure_max"              # cmH2O
    IPAP_RANGE = "ipap_range"                  # cmH2O (BiPAP)
    EPAP_RANGE = "epap_range"                  # cmH2O (BiPAP)
    RAMP_TIME = "ramp_time"                    # min
    RAMP_START_PRESSURE = "ramp_start_pressure"  # cmH2O
    WEIGHT = "weight"                          # kg
    DIMENSIONS = "dimensions"                  # mm (string-ish; keep raw)
    NOISE_LEVEL = "noise_level"                # dB(A)
    HUMIDIFIER_LEVELS = "humidifier_levels"    # count
    POWER_CONSUMPTION = "power_consumption"    # W
    OTHER = "other"


class Provenance(BaseModel):
    source_doc: str = Field(..., description="Source file name")
    page: int = Field(..., description="1-based page number the value was read from")
    snippet: Optional[str] = Field(None, description="Verbatim text the value came from (for audit)")


class Quantity(BaseModel):
    """A measured parameter. Numbers are NEVER stored as free text."""
    parameter: ParameterName
    raw_label: str = Field(..., description="Exact label as printed in the datasheet")
    value: Optional[float] = None      # single value (e.g. max pressure 20)
    min: Optional[float] = None        # range low
    max: Optional[float] = None        # range high
    default: Optional[float] = None    # factory default (query type #2)
    raw_unit: str = Field(..., description="Unit exactly as printed, e.g. 'cmH2O', 'kg', 'min'")
    provenance: Provenance

    # Filled by ontology/units.py during normalization:
    unit_ucum: Optional[str] = None
    qudt_kind: Optional[str] = None
    canonical_min: Optional[float] = None   # SI-canonical for cross-vendor numeric compare (#6/#7)
    canonical_max: Optional[float] = None
    canonical_value: Optional[float] = None


class Feature(BaseModel):
    name: str = Field(..., description="Feature name as the model labelled it")
    raw_label: str
    supported: Optional[bool] = None
    detail: Optional[str] = None       # e.g. enum value / qualifier
    provenance: Provenance

    # Filled by ontology/features.normalize during extraction — the controlled vocab key
    # the graph + query layer filter on (e.g. 'cellular_connectivity').
    key: Optional[str] = None


class TherapyMode(BaseModel):
    name: str = Field(..., description="e.g. 'CPAP', 'APAP', 'BiPAP', 'BiPAP ST', 'VAuto'")
    provenance: Provenance


class DeviceType(str, Enum):
    """Used by the relevance gate — only PAP therapy devices belong in the graph.
    Non-PAP products (masks, humidifiers-as-products, O2 concentrators, nebulizers,
    ventilators, accessories) are extracted as OTHER and dropped at ingestion."""
    CPAP = "cpap"
    APAP = "apap"
    BIPAP = "bipap"
    OTHER = "other"


# Device types we persist to the graph (the relevance gate).
PAP_DEVICE_TYPES = {DeviceType.CPAP, DeviceType.APAP, DeviceType.BIPAP}


class DeviceRecord(BaseModel):
    """Top-level extraction unit, one (resolved) device with all its evidence."""
    vendor: str
    model: str
    device_type: DeviceType = DeviceType.OTHER   # drives the relevance gate
    canonical_name: Optional[str] = None   # filled by entity resolution
    aliases: list[str] = Field(default_factory=list)
    parameters: list[Quantity] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    modes: list[TherapyMode] = Field(default_factory=list)


# Convenience: constraints/indexes to create at load time (graph/client.ensure_schema).
SCHEMA_CONSTRAINTS = [
    "CREATE CONSTRAINT device_name IF NOT EXISTS FOR (d:Device) REQUIRE d.canonical_name IS UNIQUE",
    "CREATE CONSTRAINT vendor_name IF NOT EXISTS FOR (v:Vendor) REQUIRE v.name IS UNIQUE",
    "CREATE CONSTRAINT mode_name IF NOT EXISTS FOR (m:Mode) REQUIRE m.name IS UNIQUE",
    "CREATE CONSTRAINT feature_key IF NOT EXISTS FOR (f:Feature) REQUIRE f.key IS UNIQUE",
    "CREATE CONSTRAINT doc_name IF NOT EXISTS FOR (doc:Document) REQUIRE doc.name IS UNIQUE",
    # Composite index supports query type #6 (numeric range filtering on canonical units).
    "CREATE INDEX quantity_param IF NOT EXISTS FOR (q:Quantity) ON (q.parameter)",
]
