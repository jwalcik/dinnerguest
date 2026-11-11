"""Deterministic Grocery List Aggregator & Converter.

Combines ingredient lists from one or more recipes, normalizes names,
merges identical ingredients (summing quantities with compatible units),
converts to store-friendly packaging approximations, and categorises by
supermarket aisle.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from dinnerguest.tools.scaler_tool import (
    ALL_KNOWN_UNITS,
    PATTERN_NO_UNIT,
    PATTERN_WITH_UNIT,
    VOLUME_TO_TSP,
    WEIGHT_TO_LBS,
    RecipeScaler,
)
from dinnerguest.tools.pantry_search_tool import normalize_ingredient_phrase


# ──────────────────────────────────────────────────────────────
# Supermarket aisle classification
# ──────────────────────────────────────────────────────────────
AISLE_MAP: Dict[str, List[str]] = {
    "Produce": [
        "apple", "apples", "tomato", "tomatoes", "onion", "yellow onion",
        "bell pepper", "bell peppers", "garlic", "basil", "parsley", "cilantro",
        "scallion", "scallions", "shallot", "shallots", "jalapeño", "jalapeño",
        "lime", "lemon", "lettuce", "avocado", "carrot", "carrots", "celery",
        "potato", "potatoes", "ginger", "mushroom", "mushrooms", "zucchini",
        "spinach", "kale", "broccoli",
    ],
    "Meat & Seafood": [
        "ground beef", "ground pork", "pork shoulder", "chicken breast",
        "chicken thigh", "chicken thighs", "steak", "salmon", "shrimp",
        "bacon", "sausage", "turkey", "lamb",
    ],
    "Dairy & Eggs": [
        "butter", "unsalted butter", "egg", "eggs", "milk", "cream",
        "heavy cream", "sour cream", "cheese", "parmesan", "cheddar",
        "mozzarella", "cream cheese", "yogurt",
    ],
    "Bakery & Bread": [
        "brioche", "burger bun", "burger buns", "bread", "tortilla",
        "tortillas", "corn tortillas", "pita",
    ],
    "Pasta, Rice & Grains": [
        "pasta", "dry pasta", "noodles", "wheat noodles", "rice", "cooked rice",
        "oats", "rolled oats", "quinoa",
    ],
    "Canned & Jarred": [
        "marinara sauce", "tomato sauce", "diced tomatoes", "crushed tomatoes",
        "chicken broth", "broth", "coconut milk", "beans",
    ],
    "Baking": [
        "flour", "all-purpose flour", "sugar", "granulated sugar",
        "brown sugar", "baking soda", "baking powder", "vanilla extract",
        "chocolate chips", "semi-sweet chocolate chips", "cocoa",
    ],
    "Oils & Vinegars": [
        "olive oil", "extra-virgin olive oil", "vegetable oil", "sesame oil",
        "toasted sesame oil", "chili crisp oil", "vinegar", "balsamic",
    ],
    "Spices & Seasonings": [
        "salt", "sea salt", "pepper", "black pepper", "oregano",
        "dried oregano", "garlic powder", "cumin", "ground cumin",
        "chili powder", "cinnamon", "ground cinnamon", "paprika",
        "cayenne", "thyme", "rosemary",
    ],
    "Condiments & Sauces": [
        "soy sauce", "worcestershire sauce", "tahini", "hot sauce",
        "ketchup", "mustard", "mayonnaise",
    ],
    "Nuts, Seeds & Snacks": [
        "pine nuts", "peanuts", "almonds", "walnuts", "breadcrumbs", "panko",
    ],
}

# Build reverse lookup: normalised ingredient token → aisle name
_INGREDIENT_TO_AISLE: Dict[str, str] = {}
for _aisle, _items in AISLE_MAP.items():
    for _item in _items:
        _INGREDIENT_TO_AISLE[_item.lower()] = _aisle


# ──────────────────────────────────────────────────────────────
# Unit conversion helpers
# ──────────────────────────────────────────────────────────────
VOLUME_UNITS = set(VOLUME_TO_TSP.keys())
WEIGHT_UNITS = set(WEIGHT_TO_LBS.keys())


def _units_compatible(u1: str, u2: str) -> bool:
    """Returns True if both units belong to the same measurement system."""
    u1, u2 = u1.lower(), u2.lower()
    if u1 == u2:
        return True
    if u1 in VOLUME_UNITS and u2 in VOLUME_UNITS:
        return True
    if u1 in WEIGHT_UNITS and u2 in WEIGHT_UNITS:
        return True
    return False


def _convert_to_base(qty: float, unit: str) -> Tuple[float, str]:
    """Converts a quantity to the base unit of its system (tsp / lbs)."""
    u = unit.lower()
    if u in VOLUME_TO_TSP:
        return qty * VOLUME_TO_TSP[u], "tsp"
    if u in WEIGHT_TO_LBS:
        return qty * WEIGHT_TO_LBS[u], "lbs"
    return qty, unit


def _convert_from_base(qty: float, base_unit: str) -> Tuple[float, str]:
    """Converts a base-unit quantity back to the most readable culinary unit."""
    if base_unit == "tsp":
        # Ascending thresholds: tsp < tbsp < cup < quart < gallon
        if qty >= 768:
            return qty / 768.0, "gallons"
        if qty >= 192:
            return qty / 192.0, "quarts"
        if qty >= 48:
            return qty / 48.0, "cups"
        if qty >= 3:
            return qty / 3.0, "tbsp"
        return qty, "tsp"
    if base_unit == "lbs":
        if qty < 1.0 / 16.0:
            return qty * 453.592, "grams"
        if qty < 1.0:
            return qty * 16.0, "oz"
        return qty, "lbs"
    return qty, base_unit


def get_packaging_equivalent(
    canonical_name: str,
    quantity: Optional[float],
    unit: Optional[str],
) -> Optional[str]:
    """Provides practical supermarket packaging estimates for common ingredients."""
    name = canonical_name.lower()
    u = (unit or "").lower()

    # Butter: 1 cup = 2 sticks (1/2 lb)
    if "butter" in name and u in ("cup", "cups"):
        if quantity is not None:
            sticks = quantity * 2.0
            stick_str = f"{sticks:g}" if sticks == int(sticks) else f"{sticks:.1f}"
            return f"approx {stick_str} stick{'s' if sticks != 1 else ''} of butter ({quantity * 0.5:g} lb)"

    # Eggs
    if "egg" in name:
        if quantity is not None:
            if quantity <= 6:
                return "1 half-dozen carton or 1 dozen carton"
            elif quantity <= 12:
                return "1 dozen carton"
            else:
                return "1-2 dozen cartons"

    # Garlic
    if "garlic" in name:
        if u in ("clove", "cloves") or u == "":
            if quantity and quantity >= 6:
                return "1 whole head of garlic"
            else:
                return "1 small head of garlic"

    # Dry pasta / noodles
    if any(k in name for k in ("pasta", "noodles")):
        if u in ("oz", "ounce", "ounces"):
            if quantity and quantity <= 16:
                return "1 standard box/bag (16 oz)"
            elif quantity:
                boxes = math.ceil(quantity / 16.0)
                return f"{boxes} standard boxes (16 oz each)"

    # Canned / jarred sauces & tomatoes
    if any(k in name for k in ("marinara", "crushed tomatoes", "diced tomatoes", "tomato sauce")):
        if u in ("cup", "cups"):
            if quantity and quantity <= 2:
                return "approx 1 standard 15-oz or 16-oz can/jar"
            elif quantity:
                return f"approx {math.ceil(quantity / 2)} standard 15-oz or 16-oz cans/jars"

    # Tortillas / Buns
    if "tortilla" in name:
        return "1 standard pack (10-30 count)"
    if "bun" in name or "burger bun" in name:
        return "1 standard pack (usually 4-8 count)"

    # Carrots
    if "carrot" in name and u in ("cup", "cups"):
        if quantity:
            count = int(round(quantity * 2.5))
            return f"approx {count} medium carrots (~{quantity * 0.5:.1f} lbs)"

    # Onions
    if "onion" in name and (u in ("medium", "large", "small") or not u):
        if quantity and quantity > 0:
            return f"{int(math.ceil(quantity))} fresh onion{'s' if quantity > 1 else ''}"

    return None


# ──────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────
@dataclass
class GroceryItem:
    """One line on the shopping list."""
    canonical_name: str
    display_quantity: str  # e.g. "1 1/2 cups"
    quantity: Optional[float]  # numeric total in output unit
    unit: Optional[str]
    aisle: str  # supermarket section
    source_recipes: List[str]  # recipe titles that contributed
    packaging_note: Optional[str] = None  # e.g. "approx 2 sticks of butter (1/2 lb)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "display_quantity": self.display_quantity,
            "quantity": round(self.quantity, 3) if self.quantity is not None else None,
            "unit": self.unit,
            "aisle": self.aisle,
            "packaging_note": self.packaging_note,
            "source_recipes": self.source_recipes,
        }


@dataclass
class GroceryList:
    """Aggregated shopping list across one or more recipes."""
    items: List[GroceryItem] = field(default_factory=list)
    recipe_count: int = 0

    @property
    def aisle_groups(self) -> Dict[str, List[GroceryItem]]:
        groups: Dict[str, List[GroceryItem]] = {}
        for item in self.items:
            groups.setdefault(item.aisle, []).append(item)
        return groups

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_count": self.recipe_count,
            "total_items": len(self.items),
            "items": [i.to_dict() for i in self.items],
            "by_aisle": {
                aisle: [i.to_dict() for i in items]
                for aisle, items in sorted(self.aisle_groups.items())
            },
        }


# ──────────────────────────────────────────────────────────────
# Grocery List Aggregator
# ──────────────────────────────────────────────────────────────
class GroceryListAggregatorTool:
    """Combines ingredient lists from multiple recipes into one shopping list.

    Features:
      - Normalises ingredient names for deduplication
      - Sums compatible quantities (e.g. 1 cup + 0.5 cup = 1.5 cups)
      - Converts results to the most readable culinary unit
      - Classifies every item into a supermarket aisle
    """

    def __init__(self) -> None:
        self._scaler = RecipeScaler()

    # ── public API ──────────────────────────────────────────

    def generate(
        self,
        recipe_ingredient_lists: List[Dict[str, Any]],
    ) -> GroceryList:
        """Generate an aggregated grocery list.

        Args:
            recipe_ingredient_lists: List of dicts, each with keys:
                - title (str): recipe name
                - ingredients (List[str]): raw ingredient strings
                - scale_factor (float, optional): multiplier if the recipe was scaled

        Returns:
            A ``GroceryList`` ready for display.
        """
        # Accumulator: canonical_name -> {base_qty, base_unit, sources, raw_unit}
        bucket: Dict[str, Dict[str, Any]] = {}

        recipe_count = 0
        for recipe_entry in recipe_ingredient_lists:
            title = recipe_entry.get("title", "Untitled")
            ingredients = recipe_entry.get("ingredients", [])
            scale = recipe_entry.get("scale_factor", 1.0)
            recipe_count += 1

            for raw_ing in ingredients:
                parsed = self._parse_ingredient(raw_ing)
                canon = parsed["name"]
                qty = parsed["quantity"]
                unit = parsed["unit"]

                if qty is not None and scale != 1.0:
                    qty *= scale

                key = canon.lower()

                if key not in bucket:
                    bucket[key] = {
                        "canonical_name": canon,
                        "base_qty": None,
                        "base_unit": None,
                        "raw_unit": unit,
                        "sources": [],
                    }

                entry = bucket[key]
                if title not in entry["sources"]:
                    entry["sources"].append(title)

                # Merge quantity
                if qty is not None:
                    if unit:
                        new_base_qty, new_base_unit = _convert_to_base(qty, unit)
                    else:
                        new_base_qty, new_base_unit = qty, ""

                    if entry["base_qty"] is None:
                        entry["base_qty"] = new_base_qty
                        entry["base_unit"] = new_base_unit
                        entry["raw_unit"] = unit
                    elif entry["base_unit"] == new_base_unit:
                        entry["base_qty"] += new_base_qty
                    elif _units_compatible(entry["raw_unit"] or "", unit or ""):
                        # Convert both to shared base unit and sum
                        entry["base_qty"] += new_base_qty
                        # base_unit already matches since they're compatible
                    else:
                        # Incompatible units — just sum the numeric values
                        # and keep the existing unit (best effort)
                        entry["base_qty"] += new_base_qty
                else:
                    # No quantity – keep the entry but don't add numbers
                    pass

        # Build final list
        items: List[GroceryItem] = []
        for key, entry in bucket.items():
            canon = entry["canonical_name"]
            aisle = self._classify_aisle(canon)
            base_qty = entry["base_qty"]
            base_unit = entry["base_unit"]
            raw_unit = entry["raw_unit"]

            if base_qty is not None and base_unit:
                display_qty, display_unit = _convert_from_base(base_qty, base_unit)
                qty_str = self._scaler.format_fraction(display_qty)
                display = f"{qty_str} {display_unit}" if display_unit else qty_str
            elif base_qty is not None:
                display_qty = base_qty
                display_unit = raw_unit
                qty_str = self._scaler.format_fraction(display_qty)
                display = f"{qty_str} {display_unit}" if display_unit else qty_str
            else:
                display = "as needed"
                display_qty = None
                display_unit = None

            pkg_note = get_packaging_equivalent(canon, display_qty, display_unit)

            items.append(GroceryItem(
                canonical_name=canon,
                display_quantity=display,
                quantity=display_qty if display_qty is not None else None,
                unit=display_unit,
                aisle=aisle,
                source_recipes=entry["sources"],
                packaging_note=pkg_note,
            ))

        # Sort by aisle then name for a logical shopping route
        items.sort(key=lambda i: (i.aisle, i.canonical_name))

        return GroceryList(items=items, recipe_count=recipe_count)

    # ── internal helpers ────────────────────────────────────

    def _parse_ingredient(self, raw: str) -> Dict[str, Any]:
        """Parses a raw ingredient string into quantity, unit, and canonical name."""
        raw_clean = raw.strip()
        match = PATTERN_WITH_UNIT.match(raw_clean) or PATTERN_NO_UNIT.match(raw_clean)

        if not match:
            name = normalize_ingredient_phrase(raw_clean)
            return {"quantity": None, "unit": None, "name": name}

        groups = match.groupdict()
        num_str = groups.get("num")
        mixed_whole = groups.get("mixed_whole")
        unit_str = groups.get("unit") or ""
        name_str = (groups.get("name") or "").strip()

        qty = self._scaler.parse_quantity(num_str, mixed_whole)
        canonical_name = normalize_ingredient_phrase(name_str) if name_str else normalize_ingredient_phrase(raw_clean)

        return {
            "quantity": qty,
            "unit": unit_str if unit_str else None,
            "name": canonical_name,
        }

    def _classify_aisle(self, canonical_name: str) -> str:
        """Classifies an ingredient into a supermarket aisle."""
        lower = canonical_name.lower()

        # Direct lookup first
        if lower in _INGREDIENT_TO_AISLE:
            return _INGREDIENT_TO_AISLE[lower]

        # Substring match
        for name, aisle in _INGREDIENT_TO_AISLE.items():
            if name in lower or lower in name:
                return aisle

        # Fallback heuristic using keywords
        if any(kw in lower for kw in ["oil", "vinegar"]):
            return "Oils & Vinegars"
        if any(kw in lower for kw in ["salt", "pepper", "spice", "powder", "dried", "cumin", "paprika"]):
            return "Spices & Seasonings"
        if any(kw in lower for kw in ["sauce", "soy", "worcestershire"]):
            return "Condiments & Sauces"
        if any(kw in lower for kw in ["flour", "sugar", "baking", "vanilla", "chocolate"]):
            return "Baking"
        if any(kw in lower for kw in ["beef", "pork", "chicken", "turkey", "lamb", "fish", "shrimp"]):
            return "Meat & Seafood"
        if any(kw in lower for kw in ["butter", "egg", "milk", "cream", "cheese", "yogurt"]):
            return "Dairy & Eggs"
        if any(kw in lower for kw in ["pasta", "noodle", "rice", "oat", "grain", "quinoa"]):
            return "Pasta, Rice & Grains"
        if any(kw in lower for kw in ["bread", "bun", "tortilla", "pita"]):
            return "Bakery & Bread"
        if any(kw in lower for kw in ["broth", "canned", "can ", "jar"]):
            return "Canned & Jarred"
        if any(kw in lower for kw in ["nut", "seed", "almond", "walnut", "breadcrumb", "panko"]):
            return "Nuts, Seeds & Snacks"

        return "Other"
