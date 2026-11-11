"""Deterministic Recipe Scaling Engine.

Scales ingredient quantities proportionally while enforcing culinary physical bounds:
- Minimum precision warning (< 1/8 tsp) with recommended minimum yield calculation.
- Maximum bulk warnings (> 15 lbs or > 1 gallon) with batching guidance.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple


# Conversion constants to standard base units (teaspoon for volume, pound for weight)
VOLUME_TO_TSP: Dict[str, float] = {
    "tsp": 1.0,
    "teaspoon": 1.0,
    "teaspoons": 1.0,
    "tbsp": 3.0,
    "tablespoon": 3.0,
    "tablespoons": 3.0,
    "fl oz": 6.0,
    "fluid ounce": 6.0,
    "fluid ounces": 6.0,
    "cup": 48.0,
    "cups": 48.0,
    "c": 48.0,
    "pint": 96.0,
    "pints": 96.0,
    "pt": 96.0,
    "quart": 192.0,
    "quarts": 192.0,
    "qt": 192.0,
    "gallon": 768.0,
    "gallons": 768.0,
    "gal": 768.0,
    "ml": 0.202884,
    "milliliter": 0.202884,
    "milliliters": 0.202884,
    "l": 202.884,
    "liter": 202.884,
    "liters": 202.884,
}

WEIGHT_TO_LBS: Dict[str, float] = {
    "lb": 1.0,
    "lbs": 1.0,
    "pound": 1.0,
    "pounds": 1.0,
    "oz": 1.0 / 16.0,
    "ounce": 1.0 / 16.0,
    "ounces": 1.0 / 16.0,
    "g": 1.0 / 453.592,
    "gram": 1.0 / 453.592,
    "grams": 1.0 / 453.592,
    "kg": 2.20462,
    "kilogram": 2.20462,
    "kilograms": 2.20462,
}

MIN_TSP_THRESHOLD = 0.125  # 1/8 tsp
MAX_WEIGHT_LBS_THRESHOLD = 15.0  # 15 lbs
MAX_VOLUME_GALLON_THRESHOLD = 1.0  # 1 gallon (768 tsp)


ALL_KNOWN_UNITS = sorted(
    list(VOLUME_TO_TSP.keys())
    + list(WEIGHT_TO_LBS.keys())
    + [
        "pinch", "pinches", "dash", "dashes", "clove", "cloves", "can", "cans",
        "package", "packages", "slice", "slices", "stalk", "stalks", "head",
        "heads", "bunch", "bunches", "piece", "pieces", "bag", "bags", "jar", "jars"
    ],
    key=len,
    reverse=True,
)

UNITS_REGEX_STR = r"\b(?P<unit>" + "|".join(re.escape(u) for u in ALL_KNOWN_UNITS) + r")\b"

PATTERN_WITH_UNIT = re.compile(
    r"^(?:(?P<mixed_whole>\d+)\s+)?(?P<num>\d+(?:\.\d+)?(?:/\d+)?)\s*"
    + UNITS_REGEX_STR
    + r"\s*(?:of\s+)?(?P<name>.*)$",
    re.IGNORECASE,
)

PATTERN_NO_UNIT = re.compile(
    r"^(?:(?P<mixed_whole>\d+)\s+)?(?P<num>\d+(?:\.\d+)?(?:/\d+)?)\s+(?:of\s+)?(?P<name>.*)$",
    re.IGNORECASE,
)


@dataclass
class ScaledIngredient:
    original_text: str
    scaled_text: str
    quantity: Optional[float]
    unit: Optional[str]
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_text": self.original_text,
            "scaled_text": self.scaled_text,
            "quantity": self.quantity,
            "unit": self.unit,
            "name": self.name,
        }


@dataclass
class ScalingWarning:
    warning_type: str  # "MIN_VOLUME_DIFFICULTY" or "MAX_BULK_BATCH_DIFFICULTY"
    ingredient: str
    message: str
    suggested_minimum_servings: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_type": self.warning_type,
            "ingredient": self.ingredient,
            "message": self.message,
            "suggested_minimum_servings": self.suggested_minimum_servings,
        }


@dataclass
class ScaledRecipeResult:
    recipe_title: str
    original_servings: int
    target_servings: int
    scale_factor: float
    scaled_ingredients: List[ScaledIngredient] = field(default_factory=list)
    warnings: List[ScalingWarning] = field(default_factory=list)
    recommended_minimum_servings: Optional[int] = None

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_title": self.recipe_title,
            "original_servings": self.original_servings,
            "target_servings": self.target_servings,
            "scale_factor": round(self.scale_factor, 3),
            "scaled_ingredients": [i.to_dict() for i in self.scaled_ingredients],
            "warnings": [w.to_dict() for w in self.warnings],
            "recommended_minimum_servings": self.recommended_minimum_servings,
        }


class RecipeScaler:
    """Parses, scales, and validates recipe ingredients against physical cooking bounds."""

    def parse_quantity(self, qty_str: str, mixed_whole: Optional[str] = None) -> Optional[float]:
        try:
            val = 0.0
            if mixed_whole:
                val += float(mixed_whole)
            if "/" in qty_str:
                val += float(Fraction(qty_str))
            else:
                val += float(qty_str)
            return val
        except (ValueError, ZeroDivisionError):
            return None

    def format_fraction(self, val: float) -> str:
        """Converts float back to standard culinary fraction representation."""
        if val <= 0:
            return "0"

        whole = int(val)
        remainder = val - whole

        # Snap to nearest culinary fractions (1/8, 1/4, 1/3, 1/2, 2/3, 3/4)
        fractions_map = [
            (0.125, "1/8"),
            (0.25, "1/4"),
            (0.333, "1/3"),
            (0.5, "1/2"),
            (0.667, "2/3"),
            (0.75, "3/4"),
        ]

        fraction_str = ""
        for frac_val, frac_label in fractions_map:
            if abs(remainder - frac_val) < 0.06:
                fraction_str = frac_label
                break

        if not fraction_str and remainder > 0.08 and remainder < 0.92:
            # Approximate via Fraction with max denominator 16
            f = Fraction(remainder).limit_denominator(16)
            fraction_str = f"{f.numerator}/{f.denominator}"
        elif remainder >= 0.92:
            whole += 1
            fraction_str = ""

        if whole > 0 and fraction_str:
            return f"{whole} {fraction_str}"
        elif whole > 0:
            return str(whole)
        elif fraction_str:
            return fraction_str
        else:
            return f"{val:.2f}".rstrip("0").rstrip(".")

    def scale_recipe(
        self,
        recipe_title: str,
        ingredients: List[str],
        original_servings: int,
        target_servings: int,
    ) -> ScaledRecipeResult:
        if original_servings <= 0:
            raise ValueError("original_servings must be greater than zero.")
        if target_servings <= 0:
            raise ValueError("target_servings must be greater than zero.")

        scale_factor = target_servings / original_servings
        result = ScaledRecipeResult(
            recipe_title=recipe_title,
            original_servings=original_servings,
            target_servings=target_servings,
            scale_factor=scale_factor,
        )

        min_servings_recommendations: List[int] = []

        for raw_ing in ingredients:
            raw_clean = raw_ing.strip()
            match = PATTERN_WITH_UNIT.match(raw_clean) or PATTERN_NO_UNIT.match(raw_clean)

            if not match:
                # Non-quantified item (e.g., "Salt and black pepper to taste")
                result.scaled_ingredients.append(
                    ScaledIngredient(
                        original_text=raw_clean,
                        scaled_text=raw_clean,
                        quantity=None,
                        unit=None,
                        name=raw_clean,
                    )
                )
                continue

            groups = match.groupdict()
            num_str = groups.get("num")
            mixed_whole = groups.get("mixed_whole")
            unit_str = groups.get("unit") or ""
            name_str = (groups.get("name") or "").strip()

            base_qty = self.parse_quantity(num_str, mixed_whole)
            if base_qty is None:
                result.scaled_ingredients.append(
                    ScaledIngredient(
                        original_text=raw_clean,
                        scaled_text=raw_clean,
                        quantity=None,
                        unit=None,
                        name=raw_clean,
                    )
                )
                continue

            scaled_qty = base_qty * scale_factor
            unit_clean = unit_str.lower().strip()

            # Bound checks: Volume
            if unit_clean in VOLUME_TO_TSP:
                orig_tsp = base_qty * VOLUME_TO_TSP[unit_clean]
                scaled_tsp = orig_tsp * scale_factor

                # Check minimum volume (< 1/8 tsp)
                if scaled_tsp < MIN_TSP_THRESHOLD:
                    # Calculate smallest servings where scaled_tsp >= 0.125
                    min_factor = MIN_TSP_THRESHOLD / orig_tsp
                    safe_min_servings = math.ceil(original_servings * min_factor)
                    min_servings_recommendations.append(safe_min_servings)

                    result.warnings.append(
                        ScalingWarning(
                            warning_type="MIN_VOLUME_DIFFICULTY",
                            ingredient=raw_clean,
                            message=(
                                f"Scaling down yields less than 1/8 tsp of '{name_str or raw_clean}' "
                                f"(approx {scaled_tsp:.3f} tsp), making precise home measurement difficult. "
                                f"Recommended minimum scale is {safe_min_servings} servings."
                            ),
                            suggested_minimum_servings=safe_min_servings,
                        )
                    )

                # Check maximum bulk volume (> 1 gallon)
                scaled_gallons = scaled_tsp / 768.0
                if scaled_gallons > MAX_VOLUME_GALLON_THRESHOLD:
                    result.warnings.append(
                        ScalingWarning(
                            warning_type="MAX_BULK_BATCH_DIFFICULTY",
                            ingredient=raw_clean,
                            message=(
                                f"Scaling up requires {scaled_gallons:.1f} gallons of '{name_str or raw_clean}'. "
                                "You may need commercial preparation vessels or to prepare this dish in separate batches."
                            ),
                        )
                    )

            # Bound checks: Weight
            elif unit_clean in WEIGHT_TO_LBS:
                orig_lbs = base_qty * WEIGHT_TO_LBS[unit_clean]
                scaled_lbs = orig_lbs * scale_factor

                if scaled_lbs > MAX_WEIGHT_LBS_THRESHOLD:
                    result.warnings.append(
                        ScalingWarning(
                            warning_type="MAX_BULK_BATCH_DIFFICULTY",
                            ingredient=raw_clean,
                            message=(
                                f"Scaling up requires {scaled_lbs:.1f} lbs of '{name_str or raw_clean}'. "
                                "This exceeds standard residential cookware capacity (15 lbs); consider preparing in batches."
                            ),
                        )
                    )

            # Format scaled output text
            formatted_qty = self.format_fraction(scaled_qty)
            unit_display = f" {unit_str}" if unit_str else ""
            scaled_text = f"{formatted_qty}{unit_display} {name_str}".strip()

            result.scaled_ingredients.append(
                ScaledIngredient(
                    original_text=raw_clean,
                    scaled_text=scaled_text,
                    quantity=round(scaled_qty, 3),
                    unit=unit_str or None,
                    name=name_str,
                )
            )

        if min_servings_recommendations:
            result.recommended_minimum_servings = max(min_servings_recommendations)

        return result
