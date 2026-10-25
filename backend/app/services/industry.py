"""Industry presets. Behaviour is never hard-coded per industry: each preset only
switches configurable modules on or off and supplies starter data. Every flag
can be changed afterwards in Settings."""
from copy import deepcopy

INDUSTRIES = {
    "retail": "Retail",
    "wholesale": "Wholesale / distribution",
    "manufacturing": "Manufacturing",
    "construction": "Construction",
    "hospitality": "Restaurant / Hospitality",
    "healthcare": "Healthcare",
    "agriculture": "Agriculture",
    "education": "Education",
    "automotive": "Automotive / spare parts",
    "field_service": "Field service",
    "ecommerce": "E-commerce",
    "office": "Office administration",
    "other": "Other",
}

BASE_SETTINGS = {
    "allow_negative_stock": False,
    "modules": {
        "barcodes": True,
        "variants": False,
        "batches": False,
        "expiry": False,
        "serials": False,
        "multi_location": False,     # show bins/aisles UI
        "production": False,         # raw materials + production movements
        "transfer_approval": False,  # require approval step for transfers
        "pos": False,                # counter checkout, till shifts, receipts
    },
    "expiry_warning_days": [7, 30, 90],
}

PRESETS: dict[str, dict] = {
    "retail": {"modules": {"barcodes": True, "variants": True, "pos": True}},
    "ecommerce": {"modules": {"barcodes": True, "variants": True, "pos": True}},
    "wholesale": {"modules": {"barcodes": True, "multi_location": True, "batches": True}},
    "manufacturing": {"modules": {"production": True, "batches": True, "multi_location": True}},
    "construction": {"modules": {"transfer_approval": True}, "default_site_type": "site"},
    "hospitality": {"modules": {"expiry": True, "batches": True, "pos": True}},
    "healthcare": {"modules": {"batches": True, "expiry": True, "serials": True, "pos": True}},
    "agriculture": {"modules": {"batches": True, "expiry": True}},
    "automotive": {"modules": {"serials": True, "barcodes": True}},
    "field_service": {"modules": {"serials": True, "transfer_approval": True}, "default_site_type": "vehicle"},
}

# Starter units (abbreviation, name, allow_decimals). Everyone gets the common ones.
COMMON_UNITS = [
    ("pcs", "Piece", False), ("box", "Box", False), ("ctn", "Carton", False),
    ("pack", "Pack", False), ("set", "Set", False), ("plt", "Pallet", False),
    ("kg", "Kilogram", True), ("g", "Gram", True), ("l", "Litre", True),
    ("ml", "Millilitre", True), ("m", "Metre", True),
]


def settings_for(industry: str) -> dict:
    s = deepcopy(BASE_SETTINGS)
    preset = PRESETS.get(industry, {})
    s["modules"].update(preset.get("modules", {}))
    for k, v in preset.items():
        if k != "modules":
            s[k] = v
    return s


def product_defaults(org_settings: dict) -> dict:
    """Default tracking flags for new products in this organisation."""
    m = org_settings.get("modules", {})
    return {
        "track_batches": bool(m.get("batches")) and bool(m.get("expiry")),
        "track_expiry": bool(m.get("expiry")),
    }
