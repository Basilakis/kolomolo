"""
Controlled FEATURE vocabulary — the same rigour we give parameters/units, applied to
boolean/enum capabilities.

Query types #3 (feature lookup), #4 (comparison), #6 (constraint: "cellular connectivity")
and #7 (recommendation: "with humidification") all hinge on *normalized* feature keys.
Free-text feature names from extraction are mapped to a `FeatureName` here so the graph and
the query layer speak one vocabulary regardless of vendor wording.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class FeatureName(str, Enum):
    INTEGRATED_HUMIDIFICATION = "integrated_humidification"
    HEATED_TUBE = "heated_tube"
    CELLULAR_CONNECTIVITY = "cellular_connectivity"
    WIFI_CONNECTIVITY = "wifi_connectivity"
    BLUETOOTH_CONNECTIVITY = "bluetooth_connectivity"
    EXPIRATORY_PRESSURE_RELIEF = "expiratory_pressure_relief"   # EPR / C-Flex / A-Flex / Flex
    AUTO_RAMP = "auto_ramp"
    LEAK_COMPENSATION = "leak_compensation"
    DATA_MANAGEMENT = "data_management"                         # cloud / app / patient portal
    SD_CARD = "sd_card"
    ALTITUDE_COMPENSATION = "altitude_compensation"
    AUTO_ON_OFF = "auto_on_off"                                 # SmartStart / auto start-stop
    MASK_FIT_CHECK = "mask_fit_check"
    TRAVEL_FRIENDLY = "travel_friendly"                         # explicitly marketed as travel/portable
    OTHER = "other"


# Map lowercased substrings found in raw labels -> canonical feature. First match wins,
# so order more-specific phrases before generic ones. Tune as the corpus reveals wording.
_SYNONYMS: list[tuple[tuple[str, ...], FeatureName]] = [
    (("integrated humid", "built-in humid", "built in humid", "humidif"), FeatureName.INTEGRATED_HUMIDIFICATION),
    (("heated tube", "heated tubing", "climateline", "heated hose"), FeatureName.HEATED_TUBE),
    (("cellular", "modem", "3g", "4g", "lte"), FeatureName.CELLULAR_CONNECTIVITY),
    (("wifi", "wi-fi", "wireless lan"), FeatureName.WIFI_CONNECTIVITY),
    (("bluetooth", "ble"), FeatureName.BLUETOOTH_CONNECTIVITY),
    (("epr", "c-flex", "a-flex", "bi-flex", "flex pressure", "expiratory relief",
      "pressure relief"), FeatureName.EXPIRATORY_PRESSURE_RELIEF),
    (("auto ramp", "smartramp", "smart ramp", "autoramp"), FeatureName.AUTO_RAMP),
    (("leak compensation", "leak detection", "auto leak"), FeatureName.LEAK_COMPENSATION),
    (("myair", "dreammapper", "cloud", "patient app", "data management", "encore"), FeatureName.DATA_MANAGEMENT),
    (("sd card", "sd-card", "memory card"), FeatureName.SD_CARD),
    (("altitude", "automatic altitude"), FeatureName.ALTITUDE_COMPENSATION),
    (("smartstart", "auto on", "auto-on", "auto start", "auto stop", "auto-off"), FeatureName.AUTO_ON_OFF),
    (("mask fit", "mask-fit", "fit check"), FeatureName.MASK_FIT_CHECK),
    (("travel", "portable", "lightweight", "compact"), FeatureName.TRAVEL_FRIENDLY),
]

# Convenience groupings used by the planner/queries.
CONNECTIVITY_FEATURES = {
    FeatureName.CELLULAR_CONNECTIVITY,
    FeatureName.WIFI_CONNECTIVITY,
    FeatureName.BLUETOOTH_CONNECTIVITY,
}


def normalize_feature(*texts: str) -> Optional[FeatureName]:
    """
    Resolve one or more raw strings (label, name, detail) to a canonical FeatureName.
    Returns None if nothing matches (caller may keep it as OTHER with the raw label).
    """
    hay = " ".join(t for t in texts if t).lower()
    if not hay:
        return None
    for needles, feat in _SYNONYMS:
        if any(n in hay for n in needles):
            return feat
    return None


def canonical_or_other(*texts: str) -> FeatureName:
    return normalize_feature(*texts) or FeatureName.OTHER
