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


# Map vendor spellings (legal entities, sub-brands) to one brand key so the same device
# from different documents resolves together. Extend as the corpus reveals variants.
_VENDOR_ALIASES = {
    "philips": "philips", "philips respironics": "philips", "philips healthcare": "philips",
    "respironics": "philips",
    "bmc": "bmc", "bmc medical": "bmc", "bmc medical co": "bmc", "bmc medical co ltd": "bmc",
    "fisher paykel": "fisher_paykel", "fisher paykel healthcare": "fisher_paykel",
    "lowenstein": "lowenstein", "lowenstein medical": "lowenstein",
    "lowenstein medical technology": "lowenstein",
    "mtts": "mtts", "medical technology transfer and services": "mtts",
    "resmed": "resmed", "resvent": "resvent", "drager": "drager",
}


def _norm_vendor(vendor: str) -> str:
    v = re.sub(r"[^a-z0-9 ]+", " ", vendor.lower())            # strip ™ ® punctuation
    v = re.sub(r"\b(inc|ltd|llc|gmbh|co|corp|company|technologies?)\b", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return _VENDOR_ALIASES.get(v, v)


def _norm_model(model: str, vendor: str) -> str:
    m = model.lower().replace(vendor.lower(), "")
    m = re.sub(r"\(.*?\)", " ", m)                              # drop parenthetical model codes
    m = m.replace("giii", "g3").replace("gii", "g2")           # roman-numeral normalization
    m = re.sub(r"\b(series|system|device|kit|with .*)\b", " ", m)  # noise; keep real distinguishers
    m = re.sub(r"[^a-z0-9]+", " ", m).strip()
    # NB: we deliberately KEEP auto/pro/plus/max/elite/go/10/11 — they distinguish real models.
    return m


def _canonical_key(rec: DeviceRecord) -> tuple[str, str]:
    return _norm_vendor(rec.vendor), _norm_model(rec.model, rec.vendor)


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
