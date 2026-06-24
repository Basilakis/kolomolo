"""
Unit normalization — the heart of "units are not free text".

Maps printed units -> UCUM code + QUDT quantity kind, and computes an SI-canonical
magnitude so values from different vendors/units are directly comparable for
query types #6 (numeric constraints) and #7 (ranking).

Uses `pint` for the actual dimensional conversion. cmH2O is registered explicitly
since it is the central CPAP pressure unit and not in pint's default registry by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pint

_ureg = pint.UnitRegistry()
# cmH2O at 4 °C ~ 98.0665 Pa. Register so pint can convert pressure to SI (Pa).
_ureg.define("cmH2O = 98.0665 * pascal = cm_H2O")


@dataclass(frozen=True)
class UnitSpec:
    ucum: str            # UCUM code
    qudt_kind: str       # QUDT quantity kind
    pint_unit: str       # name pint understands (for canonicalization)


# Map many printed spellings -> one canonical UnitSpec.
_UNIT_TABLE: dict[str, UnitSpec] = {
    # pressure
    "cmh2o": UnitSpec("cm[H2O]", "Pressure", "cmH2O"),
    "cm h2o": UnitSpec("cm[H2O]", "Pressure", "cmH2O"),
    "cmwater": UnitSpec("cm[H2O]", "Pressure", "cmH2O"),
    "hpa": UnitSpec("hPa", "Pressure", "hectopascal"),
    "mbar": UnitSpec("mbar", "Pressure", "millibar"),
    # mass
    "kg": UnitSpec("kg", "Mass", "kilogram"),
    "g": UnitSpec("g", "Mass", "gram"),
    "lb": UnitSpec("[lb_av]", "Mass", "pound"),
    "lbs": UnitSpec("[lb_av]", "Mass", "pound"),
    # time
    "min": UnitSpec("min", "Time", "minute"),
    "minutes": UnitSpec("min", "Time", "minute"),
    "s": UnitSpec("s", "Time", "second"),
    # sound
    "db": UnitSpec("dB", "SoundPressureLevel", "decibel"),
    "dba": UnitSpec("dB", "SoundPressureLevel", "decibel"),
    "db(a)": UnitSpec("dB", "SoundPressureLevel", "decibel"),
    # power
    "w": UnitSpec("W", "Power", "watt"),
    # count / dimensionless
    "levels": UnitSpec("1", "Dimensionless", "dimensionless"),
    "": UnitSpec("1", "Dimensionless", "dimensionless"),
}


def _norm_key(raw_unit: str) -> str:
    return raw_unit.strip().lower().replace("₂", "2").replace(" ", "") if raw_unit else ""


def lookup(raw_unit: str) -> Optional[UnitSpec]:
    """Resolve a printed unit string to a UnitSpec, or None if unknown."""
    key = _norm_key(raw_unit)
    # try exact, then a couple of relaxed forms
    return _UNIT_TABLE.get(key) or _UNIT_TABLE.get(key.replace("(a)", "a"))


def coerce_decimal(value) -> Optional[float]:
    """Defensive decimal-comma guard: '1,33' -> 1.33 (European datasheets). Issue #1."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return None
    return float(value)


def to_canonical(value: Optional[float], raw_unit: str) -> Optional[float]:
    """
    Convert a magnitude to its SI-canonical base so cross-vendor compares are valid.
    Returns None if value is None or the unit is unknown/non-convertible.
    """
    value = coerce_decimal(value)
    if value is None:
        return None
    spec = lookup(raw_unit)
    if spec is None:
        return None
    try:
        q = (value * _ureg(spec.pint_unit)).to_base_units()
        return float(q.magnitude)
    except Exception:
        return None


def normalize_quantity(q) -> None:
    """
    In-place enrich a schema.Quantity with UCUM/QUDT codes + canonical magnitudes.
    Call this during ingestion right after extraction, before loading to the graph.
    """
    spec = lookup(q.raw_unit)
    if spec is not None:
        q.unit_ucum = spec.ucum
        q.qudt_kind = spec.qudt_kind
    q.canonical_value = to_canonical(q.value, q.raw_unit)
    q.canonical_min = to_canonical(q.min, q.raw_unit)
    q.canonical_max = to_canonical(q.max, q.raw_unit)
