"""
Entity resolution — collapse vendor naming variants into one canonical Device.

"AirSense 11", "ResMed AirSense 11 AutoSet", "AS11" -> one node. Datasheets and
manuals for the same device must merge so comparison/recommendation see a single
entity with all its parameters/features/modes.

Strategy (cheap first, LLM only on ambiguity):
  1. normalize string (lowercase, strip vendor token, collapse whitespace)
  2. group by (vendor, normalized model)
  3. merge records in a group; keep all provenance; aliases retained
  4. [optional] LLM tie-break for near-duplicate groups
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..ontology.schema import DeviceRecord


def _canonical_key(rec: DeviceRecord) -> tuple[str, str]:
    vendor = rec.vendor.strip().lower()
    model = rec.model.strip().lower()
    model = model.replace(vendor, "")                 # drop embedded vendor name
    model = re.sub(r"\b(autoset|auto|elite|pro|plus)\b", "", model)  # common trims; tune per corpus
    model = re.sub(r"[^a-z0-9]+", " ", model).strip()
    return vendor, model


def _merge(group: list[DeviceRecord]) -> DeviceRecord:
    base = group[0]
    # prefer a specific PAP type over 'other' if any record in the group has one
    dtype = next((r.device_type for r in group if r.device_type.value != "other"), base.device_type)
    merged = DeviceRecord(
        vendor=base.vendor,
        model=base.model,
        device_type=dtype,
        canonical_name=f"{base.vendor} {base.model}".strip(),
        aliases=sorted({r.model for r in group}),
    )
    for r in group:
        merged.parameters.extend(r.parameters)
        merged.features.extend(r.features)
        merged.modes.extend(r.modes)
    # de-dup modes by name (keep first provenance)
    seen = set()
    deduped_modes = []
    for m in merged.modes:
        if m.name.lower() not in seen:
            seen.add(m.name.lower())
            deduped_modes.append(m)
    merged.modes = deduped_modes
    return merged


def resolve(records: list[DeviceRecord]) -> list[DeviceRecord]:
    groups: dict[tuple[str, str], list[DeviceRecord]] = defaultdict(list)
    for rec in records:
        groups[_canonical_key(rec)].append(rec)
    return [_merge(g) for g in groups.values()]
