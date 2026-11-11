"""Pantry & On-Hand Ingredient Search Tool.

Performs inverted index / set-intersection recipe matching against user ingredients,
detects viable culinary substitutions, and identifies missing pantry staples for follow-up dialogue.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dinnerguest.tools.allergen_tool import AllergenChecker, AllergenReport
from dinnerguest.tools.scaler_tool import ALL_KNOWN_UNITS


# Common preparation words to strip when normalizing ingredients
PREP_ADJECTIVES = {
    "fresh", "freshly", "chopped", "diced", "minced", "sliced", "thinly", "grated",
    "shredded", "crushed", "cooked", "uncooked", "softened", "melted", "warm", "cold",
    "chilled", "boneless", "skinless", "halved", "quartered", "peeled", "seeded",
    "toasted", "dry", "dried", "medium", "large", "small", "raw", "canned", "whole"
}


def normalize_ingredient_phrase(phrase: str) -> str:
    """Normalizes an ingredient string into a clean canonical keyword for set matching.

    Examples:
        '1 lb 80/20 ground beef' -> 'ground beef'
        '1 medium yellow onion, diced' -> 'yellow onion'
        '2 cloves garlic, minced' -> 'garlic'
        '0.5 cup breadcrumbs' -> 'breadcrumbs'
    """
    text = phrase.strip().lower()
    # Remove parenthetical expressions like "(optional)" or "(wheat)"
    text = re.sub(r"\(.*?\)", "", text)
    # Split on comma (e.g. "onion, diced")
    parts = text.split(",")
    main_part = parts[0].strip()

    # Strip numbers, fractions, and units
    # Match leading quantity and unit
    words = main_part.split()
    filtered_words = []
    unit_set = set(ALL_KNOWN_UNITS)

    for w in words:
        clean_w = re.sub(r"[^\w/.-]", "", w)
        if not clean_w:
            continue
        # Skip numeric digits or fractions
        if re.match(r"^[\d/.-]+$", clean_w):
            continue
        # Skip unit words
        if clean_w in unit_set:
            continue
        # Skip "of"
        if clean_w == "of":
            continue
        if clean_w in PREP_ADJECTIVES and len(words) > 2:
            continue
        filtered_words.append(clean_w)

    result = " ".join(filtered_words).strip()
    return result if result else main_part


@dataclass
class SubstitutionSuggestion:
    recipe_ingredient: str
    user_ingredient: str
    ratio: str
    note: str

    def to_dict(self) -> dict[str, str]:
        return {
            "recipe_ingredient": self.recipe_ingredient,
            "user_ingredient": self.user_ingredient,
            "ratio": self.ratio,
            "note": self.note,
        }


@dataclass
class RecipeMatch:
    recipe_id: str
    title: str
    servings: int
    match_score: float  # 0.0 to 1.0
    matched_ingredients: List[str]
    substituted_ingredients: List[SubstitutionSuggestion]
    missing_ingredients: List[str]
    missing_staples: List[str]
    raw_ingredients: List[str]
    instructions: List[str]
    allergen_report: Optional[AllergenReport] = None

    @property
    def is_exact_match(self) -> bool:
        return len(self.missing_ingredients) == 0

    @property
    def total_missing_count(self) -> int:
        return len(self.missing_ingredients)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "title": self.title,
            "servings": self.servings,
            "match_score": round(self.match_score, 2),
            "is_exact_match": self.is_exact_match,
            "matched_ingredients": self.matched_ingredients,
            "substituted_ingredients": [s.to_dict() for s in self.substituted_ingredients],
            "missing_ingredients": self.missing_ingredients,
            "missing_staples": self.missing_staples,
            "allergen_summary": self.allergen_report.to_dict() if self.allergen_report else None,
        }


@dataclass
class SearchResult:
    total_matches_count: int
    page: int
    page_size: int
    recipes: List[RecipeMatch]
    staple_followup_prompts: List[str] = field(default_factory=list)

    @property
    def has_more(self) -> bool:
        return self.total_matches_count > (self.page * self.page_size)

    @property
    def remaining_count(self) -> int:
        return max(0, self.total_matches_count - (self.page * self.page_size))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_matches_count": self.total_matches_count,
            "page": self.page,
            "page_size": self.page_size,
            "has_more": self.has_more,
            "remaining_count": self.remaining_count,
            "recipes": [r.to_dict() for r in self.recipes],
            "staple_followup_prompts": self.staple_followup_prompts,
        }


class PantrySearchTool:
    """Tool that coordinates on-hand recipe search, substitutions, and allergen screening."""

    def __init__(
        self,
        recipes_path: Optional[str | Path] = None,
        substitutions_path: Optional[str | Path] = None,
        staples_path: Optional[str | Path] = None,
        allergen_checker: Optional[AllergenChecker] = None,
    ):
        base_dir = Path(__file__).parent.parent
        self.recipes_path = Path(recipes_path or (base_dir.parent.parent / "tests" / "fixtures" / "sample_recipes.json"))
        self.substitutions_path = Path(substitutions_path or (base_dir / "data" / "substitutions_canonical.json"))
        self.staples_path = Path(staples_path or (base_dir / "data" / "pantry_staples.json"))
        self.allergen_checker = allergen_checker or AllergenChecker()

        self._load_data()

    def _load_data(self) -> None:
        # Load recipes
        if self.recipes_path.exists():
            with open(self.recipes_path, "r", encoding="utf-8") as f:
                self.recipes: List[Dict[str, Any]] = json.load(f)
        else:
            self.recipes = []

        # Load substitutions
        if self.substitutions_path.exists():
            with open(self.substitutions_path, "r", encoding="utf-8") as f:
                sub_data = json.load(f)
                self.substitutions: Dict[str, Any] = sub_data.get("substitutions", {})
        else:
            self.substitutions = {}

        # Load staples
        if self.staples_path.exists():
            with open(self.staples_path, "r", encoding="utf-8") as f:
                staple_data = json.load(f)
                self.staples: Set[str] = {s.lower() for s in staple_data.get("staples", [])}
        else:
            self.staples = set()

    def check_substitution(self, recipe_token: str, available_tokens: Set[str]) -> Optional[SubstitutionSuggestion]:
        """Checks if any available item can substitute for a missing recipe ingredient."""
        sub_info = self.substitutions.get(recipe_token)
        if not sub_info:
            # Check partial match on keys (e.g. 'shallots' vs 'shallot')
            for key, info in self.substitutions.items():
                if key in recipe_token or recipe_token in key:
                    sub_info = info
                    break

        if not sub_info:
            return None

        for candidate in sub_info.get("substitutes", []):
            candidate_norm = normalize_ingredient_phrase(candidate)
            for avail in available_tokens:
                if candidate_norm in avail or avail in candidate_norm:
                    return SubstitutionSuggestion(
                        recipe_ingredient=recipe_token,
                        user_ingredient=avail,
                        ratio=sub_info.get("ratio", "1:1"),
                        note=sub_info.get("note", f"Can substitute {avail} for {recipe_token}"),
                    )
        return None

    def is_staple(self, token: str) -> bool:
        clean = token.lower().strip()
        if clean in self.staples:
            return True
        for staple in self.staples:
            if staple in clean or clean in staple:
                return True
        return False

    def search_recipes(
        self,
        on_hand_ingredients: List[str],
        max_missing: int = 2,
        page: int = 1,
        page_size: int = 5,
        assume_basic_staples: bool = False,
    ) -> SearchResult:
        """Finds recipes matching on-hand ingredients, ranking by lowest missing items and highest coverage."""
        normalized_user_items = {normalize_ingredient_phrase(i) for i in on_hand_ingredients if i.strip()}
        # Handle "dry spices" wildcard if user mentions dry spices / spices
        has_spices_wildcard = any("spice" in i or "seasoning" in i for i in normalized_user_items)

        matches: List[RecipeMatch] = []
        staple_followup_candidates: Dict[str, int] = {}

        for recipe in self.recipes:
            raw_ingredients: List[str] = recipe.get("ingredients", [])
            if not raw_ingredients:
                continue

            matched_ings: List[str] = []
            substituted_ings: List[SubstitutionSuggestion] = []
            missing_ings: List[str] = []
            missing_staples: List[str] = []

            for raw_ing in raw_ingredients:
                norm_recipe_ing = normalize_ingredient_phrase(raw_ing)

                # Direct match check
                direct_match = False
                for user_item in normalized_user_items:
                    if user_item in norm_recipe_ing or norm_recipe_ing in user_item:
                        direct_match = True
                        break

                # Wildcard check for spices (oregano, garlic powder, cumin, pepper, salt)
                if not direct_match and has_spices_wildcard:
                    if any(s in norm_recipe_ing for s in ["salt", "pepper", "oregano", "powder", "spice", "cumin"]):
                        direct_match = True

                if direct_match:
                    matched_ings.append(raw_ing)
                    continue

                # Substitution check
                sub = self.check_substitution(norm_recipe_ing, normalized_user_items)
                if sub:
                    substituted_ings.append(sub)
                    continue

                # Missing ingredient check
                if assume_basic_staples and self.is_staple(norm_recipe_ing):
                    matched_ings.append(f"{raw_ing} (from assumed pantry staples)")
                    continue

                if self.is_staple(norm_recipe_ing):
                    missing_staples.append(norm_recipe_ing)

                missing_ings.append(raw_ing)

            # Filter by tolerance
            if len(missing_ings) <= max_missing:
                total_items = len(raw_ingredients)
                covered_items = len(matched_ings) + len(substituted_ings)
                score = covered_items / total_items if total_items > 0 else 0.0

                # Count missing staples only for viable candidate matches
                for st in missing_staples:
                    staple_followup_candidates[st] = staple_followup_candidates.get(st, 0) + 1

                # Run allergen check
                allergen_rep = self.allergen_checker.check_recipe_ingredients(raw_ingredients)

                match_entry = RecipeMatch(
                    recipe_id=recipe["id"],
                    title=recipe["title"],
                    servings=recipe.get("servings", 4),
                    match_score=score,
                    matched_ingredients=matched_ings,
                    substituted_ingredients=substituted_ings,
                    missing_ingredients=missing_ings,
                    missing_staples=missing_staples,
                    raw_ingredients=raw_ingredients,
                    instructions=recipe.get("instructions", []),
                    allergen_report=allergen_rep,
                )
                matches.append(match_entry)

        # Sort: lowest missing items first, then highest score
        matches.sort(key=lambda m: (len(m.missing_ingredients), -m.match_score))

        # Pagination: top 5 per page
        total_matches = len(matches)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_recipes = matches[start_idx:end_idx]

        # Generate staple follow-up questions
        followup_prompts: List[str] = []
        if staple_followup_candidates:
            # Pick top 1-2 most frequent missing staples across candidate recipes
            top_staples = sorted(staple_followup_candidates.keys(), key=lambda k: staple_followup_candidates[k], reverse=True)[:2]
            for st in top_staples:
                followup_prompts.append(f"Do you happen to have any {st} on hand?")

        return SearchResult(
            total_matches_count=total_matches,
            page=page,
            page_size=page_size,
            recipes=paged_recipes,
            staple_followup_prompts=followup_prompts,
        )
