"""
Persist resolved DeviceRecords into Neo4j with full provenance.

Graph shape (see ontology/schema.py):
  (Device)-[:MADE_BY]->(Vendor)
  (Device)-[:HAS_PARAMETER]->(Quantity)-[:SOURCED_FROM {page}]->(Document)
  (Device)-[:HAS_FEATURE]->(Feature)-[:SOURCED_FROM {page}]->(Document)
  (Device)-[:SUPPORTS_MODE]->(Mode)-[:SOURCED_FROM {page}]->(Document)
"""
from __future__ import annotations

from ..graph.client import GraphClient
from ..ontology.schema import DeviceRecord

_MERGE_DEVICE = """
MERGE (d:Device {canonical_name: $name})
  SET d.vendor = $vendor, d.model = $model, d.aliases = $aliases, d.device_type = $device_type
MERGE (v:Vendor {name: $vendor})
MERGE (d)-[:MADE_BY]->(v)
"""

_ADD_QUANTITY = """
MATCH (d:Device {canonical_name: $name})
MERGE (doc:Document {name: $doc})
CREATE (q:Quantity {
  parameter: $parameter, raw_label: $raw_label,
  value: $value, min: $min, max: $max, default: $default,
  raw_unit: $raw_unit, unit_ucum: $unit_ucum, qudt_kind: $qudt_kind,
  canonical_value: $cval, canonical_min: $cmin, canonical_max: $cmax
})
CREATE (d)-[:HAS_PARAMETER]->(q)
CREATE (q)-[:SOURCED_FROM {page: $page, snippet: $snippet}]->(doc)
"""

_ADD_FEATURE = """
MATCH (d:Device {canonical_name: $name})
MERGE (doc:Document {name: $doc})
MERGE (f:Feature {key: $fkey})
  SET f.name = $fname, f.raw_label = $raw_label, f.detail = $detail
CREATE (d)-[:HAS_FEATURE {supported: $supported}]->(f)
CREATE (f)-[:SOURCED_FROM {page: $page}]->(doc)
"""

_ADD_MODE = """
MATCH (d:Device {canonical_name: $name})
MERGE (doc:Document {name: $doc})
MERGE (m:Mode {name: $mname})
MERGE (d)-[:SUPPORTS_MODE]->(m)
CREATE (m)-[:SOURCED_FROM {page: $page}]->(doc)
"""


def load_records(records: list[DeviceRecord], client: GraphClient | None = None) -> int:
    client = client or GraphClient()
    client.ensure_schema()
    count = 0
    for rec in records:
        name = rec.canonical_name or f"{rec.vendor} {rec.model}".strip()
        client.run(_MERGE_DEVICE, name=name, vendor=rec.vendor, model=rec.model,
                   aliases=rec.aliases, device_type=rec.device_type.value)
        for q in rec.parameters:
            client.run(_ADD_QUANTITY, name=name, doc=q.provenance.source_doc, page=q.provenance.page,
                       snippet=q.provenance.snippet, parameter=q.parameter.value, raw_label=q.raw_label,
                       value=q.value, min=q.min, max=q.max, default=q.default, raw_unit=q.raw_unit,
                       unit_ucum=q.unit_ucum, qudt_kind=q.qudt_kind,
                       cval=q.canonical_value, cmin=q.canonical_min, cmax=q.canonical_max)
        for f in rec.features:
            client.run(_ADD_FEATURE, name=name, doc=f.provenance.source_doc, page=f.provenance.page,
                       fkey=f.key or "other", fname=f.name, raw_label=f.raw_label,
                       detail=f.detail, supported=f.supported)
        for m in rec.modes:
            client.run(_ADD_MODE, name=name, doc=m.provenance.source_doc, page=m.provenance.page,
                       mname=m.name)
        count += 1
    return count
