"""Deterministic Allergen Checker for recipes and ingredients.

Maps ingredients against canonical FDA FALCPA Big 9 and extended allergen ontologies.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class AllergenMatch:
    allergen_id: str
    display_name: str
    source_ingredient: str
    matched_term: str
    severity: str
    falpca: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "allergen_id": self.allergen_id,
            "display_name": self.display_name,
            "source_ingredient": self.source_ingredient,
            "matched_term": self.matched_term,
            "severity": self.severity,
            "falpca": self.falpca,
        }


@dataclass
class AmbiguousIngredient:
    source_ingredient: str
    flagged_term: str
    recommendation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_ingredient": self.source_ingredient,
            "flagged_term": self.flagged_term,
            "recommendation": self.recommendation,
        }


@dataclass
class AllergenReport:
    detected_allergens: Dict[str, List[AllergenMatch]] = field(default_factory=dict)
    ambiguous_ingredients: List[AmbiguousIngredient] = field(default_factory=list)
    checked_items_count: int = 0

    @property
    def has_allergens(self) -> bool:
        return len(self.detected_allergens) > 0

    @property
    def unique_allergen_names(self) -> List[str]:
        return sorted([matches[0].display_name for matches in self.detected_allergens.values()])

    @property
    def unique_allergens(self) -> List[str]:
        return self.unique_allergen_names

    def is_safe_for(self, excluded_allergens: List[str]) -> bool:
        """Returns True if none of the excluded allergens are present in detected allergens."""
        excluded_set = {a.lower().strip() for a in excluded_allergens}
        for allergen_id, matches in self.detected_allergens.items():
            display = matches[0].display_name.lower()
            if allergen_id in excluded_set or display in excluded_set:
                return False
        return True

    def get_guest_warning_summary(self) -> str:
        """Generates clear, actionable allergen advisory text for event guests."""
        if not self.has_allergens and not self.ambiguous_ingredients:
            return "No common major allergens detected based on ingredient inspection."

        warnings: List[str] = []
        if self.detected_allergens:
            allergen_names = ", ".join(self.unique_allergen_names)
            warnings.append(f"Contains: {allergen_names}.")

        if self.ambiguous_ingredients:
            items = ", ".join({amb.source_ingredient for amb in self.ambiguous_ingredients})
            warnings.append(f"Requires verification (ambiguous ingredients): {items}.")

        return " ".join(warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_allergens": self.has_allergens,
            "unique_allergens": self.unique_allergen_names,
            "detected_allergens": {
                k: [m.to_dict() for m in v] for k, v in self.detected_allergens.items()
            },
            "ambiguous_ingredients": [a.to_dict() for a in self.ambiguous_ingredients],
            "checked_items_count": self.checked_items_count,
            "guest_warning": self.get_guest_warning_summary(),
        }


class AllergenChecker:
    """Zero-hallucination deterministic allergen evaluation engine."""

    def __init__(self, data_path: Optional[str | Path] = None):
        if data_path is None:
            data_path = Path(__file__).parent.parent / "data" / "allergens_canonical.json"
        self.data_path = Path(data_path)
        self._load_taxonomy()

    def _load_taxonomy(self) -> None:
        with open(self.data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.major_allergens = data.get("major_allergens", {})
        self.compound_derivatives = data.get("compound_derivatives", {})
        self.ambiguous_tokens = data.get("ambiguous_tokens", [])

        # Build regex patterns for word-boundary matching
        self._direct_patterns: Dict[str, List[tuple[re.Pattern, str]]] = {}
        for allergen_id, info in self.major_allergens.items():
            patterns = []
            for token in info.get("direct_tokens", []):
                pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
                patterns.append((pattern, token))
            self._direct_patterns[allergen_id] = patterns

        self._compound_patterns: List[tuple[re.Pattern, str, List[str]]] = []
        for compound_term, allergen_ids in self.compound_derivatives.items():
            pattern = re.compile(rf"\b{re.escape(compound_term)}\b", re.IGNORECASE)
            self._compound_patterns.append((pattern, compound_term, allergen_ids))

        self._ambiguous_patterns: List[tuple[re.Pattern, str]] = [
            (re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE), term)
            for term in self.ambiguous_tokens
        ]

    def check_ingredient(self, ingredient_text: str) -> tuple[List[AllergenMatch], List[AmbiguousIngredient]]:
        """Scans a single ingredient string for allergens and ambiguity."""
        clean_text = ingredient_text.strip().lower()
        working_text = clean_text
        matches: List[AllergenMatch] = []
        ambiguous: List[AmbiguousIngredient] = []

        # 1. Check compound derivatives first (e.g. worcestershire sauce, pesto, almond milk)
        matched_allergens_from_compound: Set[str] = set()
        for pattern, term, allergen_ids in self._compound_patterns:
            if pattern.search(working_text):
                for a_id in allergen_ids:
                    if a_id in self.major_allergens:
                        info = self.major_allergens[a_id]
                        matches.append(
                            AllergenMatch(
                                allergen_id=a_id,
                                display_name=info["display_name"],
                                source_ingredient=ingredient_text,
                                matched_term=term,
                                severity=info["severity"],
                                falpca=info["falpca"],
                            )
                        )
                        matched_allergens_from_compound.add(a_id)
                # Mask out matched compound term to prevent sub-token collisions (e.g. "milk" in "almond milk")
                working_text = pattern.sub(lambda m: " " * len(m.group(0)), working_text)

        # 2. Check direct allergen tokens on the remaining unmasked text
        for allergen_id, patterns in self._direct_patterns.items():
            if allergen_id in matched_allergens_from_compound:
                continue  # already attributed via compound derivative
            info = self.major_allergens[allergen_id]
            for pattern, token in patterns:
                if pattern.search(working_text):
                    matches.append(
                        AllergenMatch(
                            allergen_id=allergen_id,
                            display_name=info["display_name"],
                            source_ingredient=ingredient_text,
                            matched_term=token,
                            severity=info["severity"],
                            falpca=info["falpca"],
                        )
                    )
                    working_text = pattern.sub(lambda m: " " * len(m.group(0)), working_text)
                    break

        # 3. Check for ambiguous tokens
        for pattern, term in self._ambiguous_patterns:
            if pattern.search(clean_text):
                ambiguous.append(
                    AmbiguousIngredient(
                        source_ingredient=ingredient_text,
                        flagged_term=term,
                        recommendation=f"'{term}' may conceal dairy, gluten, soy, or nightshades. Verify manufacturer label.",
                    )
                )

        return matches, ambiguous

    def check_recipe_ingredients(self, ingredients: List[str]) -> AllergenReport:
        """Evaluates an entire ingredient list and returns an AllergenReport."""
        report = AllergenReport(checked_items_count=len(ingredients))

        for raw_ing in ingredients:
            matches, ambiguous = self.check_ingredient(raw_ing)
            for m in matches:
                report.detected_allergens.setdefault(m.allergen_id, []).append(m)
            report.ambiguous_ingredients.extend(ambiguous)

        return report

    def check_menu(self, dishes: Dict[str, List[str]]) -> Dict[str, Any]:
        """Evaluates a multi-dish menu (e.g. for party planning) and produces aggregated guest guidance."""
        menu_report: Dict[str, Any] = {
            "dishes": {},
            "combined_allergens": set(),
            "overall_warning": "",
        }

        all_reports: List[AllergenReport] = []
        for dish_name, ingredients in dishes.items():
            rep = self.check_recipe_ingredients(ingredients)
            menu_report["dishes"][dish_name] = rep.to_dict()
            menu_report["combined_allergens"].update(rep.unique_allergen_names)
            all_reports.append(rep)

        combined_list = sorted(list(menu_report["combined_allergens"]))
        menu_report["combined_allergens"] = combined_list

        if combined_list:
            menu_report["overall_warning"] = (
                f"Guest Notice: The menu contains the following allergens: {', '.join(combined_list)}. "
                "Cross-contact precautions should be taken."
            )
        else:
            menu_report["overall_warning"] = "Guest Notice: No major allergens detected in this menu."

        return menu_report
