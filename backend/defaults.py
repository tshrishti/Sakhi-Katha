"""Product templates (recipes). Each template is the DEFAULT rate card for a product; a group's own approved, versioned
rate card (db.RateCard, see ratecards.py) overrides it. Adding a product type = adding one entry here."""
import re

TEMPLATES = {
    "cloth_bag": {
        "label": "Cloth bag", "unit": "bag", "words": ["bag", "bags", "jhola", "thaila", "tote"],
        "stages": [{"name": "cutting", "rate": 5, "workers": 1}, {"name": "stitching", "rate": 16, "workers": 2},
                   {"name": "finishing", "rate": 8, "workers": 2}, {"name": "packing", "rate": 4, "workers": 1}],
        "cost": {"material": 38, "minutes": 40, "hourly": 50, "overhead_pct": 8, "margin_pct": 10},
    },
    "kurta": {
        "label": "Cotton kurta", "unit": "kurta", "words": ["kurta", "kurtas", "kurti", "kurtis", "shirt", "shirts"],
        "stages": [{"name": "cutting", "rate": 12, "workers": 1}, {"name": "stitching", "rate": 45, "workers": 2},
                   {"name": "finishing", "rate": 15, "workers": 2}, {"name": "packing", "rate": 6, "workers": 1}],
        "cost": {"material": 140, "minutes": 110, "hourly": 50, "overhead_pct": 8, "margin_pct": 12},
    },
}
DEFAULT_PRODUCT = "cloth_bag"
STAGES, COST = TEMPLATES[DEFAULT_PRODUCT]["stages"], TEMPLATES[DEFAULT_PRODUCT]["cost"]   # kept for old imports
UNIT_WORDS = sorted({w for t in TEMPLATES.values() for w in t["words"]} | {"piece", "pieces", "pcs", "pc"}, key=len, reverse=True)

def detect(text):
    """Map free text ('cloth bags', 'kurti', 'Product: kurta') to a template key, or None if it matches nothing."""
    t = (text or "").lower()
    for key, tpl in TEMPLATES.items():
        if key.replace("_", " ") in t or any(re.search(rf"\b{re.escape(w)}\b", t) for w in tpl["words"]): return key
    return None
