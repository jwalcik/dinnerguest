"""Semantic Vibe & Mood Search Tool.

Matches recipes against mood, season, flavor profile, and craving descriptors,
combining semantic tag scoring with deterministic allergen screening.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dinnerguest.tools.allergen_tool import AllergenChecker, AllergenReport


VIBE_STOP_WORDS = {
    "food", "dish", "dishes", "recipe", "recipes", "make", "cook", "cooking",
    "want", "like", "need", "some", "something", "vague", "response", "please",
    "help", "looking", "would", "could", "have", "with", "what", "good", "idea",
    "ideas", "dinner", "lunch", "meal"
}


@dataclass
class VibeRecipeResult:
    recipe_id: str
    title: str
    servings: int
    prep_time_minutes: int
    cook_time_minutes: int
    tags: List[str]
    matched_vibes: List[str]
    score: float
    ingredients: List[str]
    instructions: List[str]
    allergen_report: Optional[AllergenReport] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "title": self.title,
            "servings": self.servings,
            "prep_time_minutes": self.prep_time_minutes,
            "cook_time_minutes": self.cook_time_minutes,
            "tags": self.tags,
            "matched_vibes": self.matched_vibes,
            "score": round(self.score, 2),
            "ingredients": self.ingredients,
            "instructions": self.instructions,
            "allergen_summary": self.allergen_report.to_dict() if self.allergen_report else None,
        }


class VibeSearchTool:
    """Tool that finds recipes based on moods, feelings, weather, and flavor profiles."""

    def __init__(
        self,
        recipes_path: Optional[str | Path] = None,
        taxonomy_path: Optional[str | Path] = None,
        allergen_checker: Optional[AllergenChecker] = None,
    ):
        base_dir = Path(__file__).parent.parent
        self.recipes_path = Path(recipes_path or (base_dir.parent.parent / "tests" / "fixtures" / "sample_recipes.json"))
        self.taxonomy_path = Path(taxonomy_path or (base_dir / "data" / "vibe_taxonomy.json"))
        self.allergen_checker = allergen_checker or AllergenChecker()

        self._load_data()

    def _load_data(self) -> None:
        if self.recipes_path.exists():
            with open(self.recipes_path, "r", encoding="utf-8") as f:
                self.recipes: List[Dict[str, Any]] = json.load(f)
        else:
            self.recipes = []

        if self.taxonomy_path.exists():
            with open(self.taxonomy_path, "r", encoding="utf-8") as f:
                tax_data = json.load(f)
                self.vibe_categories: Dict[str, Any] = tax_data.get("vibe_categories", {})
        else:
            self.vibe_categories = {}

    def extract_vibe_tokens(self, query: str) -> Set[str]:
        """Extracts recognized vibe tokens and synonyms from a natural language query."""
        tokens = set(re.findall(r"\b\w+\b", query.lower()))
        matched_categories: Set[str] = set()

        for category_id, info in self.vibe_categories.items():
            synonyms = set(info.get("synonyms", []))
            if tokens.intersection(synonyms):
                matched_categories.add(category_id)

        return matched_categories

    def search_by_vibe(
        self,
        query: str,
        filters: Optional[Dict[str, str]] = None,
        limit: int = 5,
    ) -> List[VibeRecipeResult]:
        """Finds recipes matching mood, taste, or flavor profile."""
        query_clean = query.lower()
        query_words = set(re.findall(r"\b\w+\b", query_clean))
        matched_vibe_cats = self.extract_vibe_tokens(query_clean)

        filters = filters or {}
        required_format = filters.get("format", "").lower()
        required_profile = filters.get("flavor_profile", "").lower()
        required_protein = filters.get("protein", "").lower()

        results: List[VibeRecipeResult] = []

        for recipe in self.recipes:
            title_lower = recipe["title"].lower()
            recipe_tags = set(t.lower() for t in recipe.get("tags", []))
            raw_text = (
                title_lower
                + " "
                + " ".join(recipe_tags)
                + " "
                + " ".join(recipe.get("instructions", [])).lower()
                + " "
                + " ".join(recipe.get("ingredients", [])).lower()
            )

            # Filtering constraints
            if required_format:
                if required_format not in raw_text:
                    continue
            if required_profile:
                if required_profile not in recipe_tags and required_profile not in raw_text:
                    continue
            if required_protein:
                if required_protein not in raw_text:
                    continue

            # Compute score
            score = 0.0
            matched_vibes_list: List[str] = []

            # Tag intersections
            for tag in recipe_tags:
                if tag in query_words or tag in matched_vibe_cats:
                    score += 2.0
                    matched_vibes_list.append(tag)

            # Query words in title
            for w in query_words:
                if w in VIBE_STOP_WORDS:
                    continue
                if len(w) > 3 and w in title_lower:
                    score += 3.0
                    matched_vibes_list.append(w)
                elif len(w) > 3 and w in raw_text:
                    score += 0.5

            # Category synonym matches
            for cat in matched_vibe_cats:
                cat_info = self.vibe_categories[cat]
                synonyms = set(cat_info.get("synonyms", []))
                if recipe_tags.intersection(synonyms):
                    score += 2.5
                    matched_vibes_list.append(cat)

            if score >= 2.0:
                allergen_rep = self.allergen_checker.check_recipe_ingredients(recipe.get("ingredients", []))
                results.append(
                    VibeRecipeResult(
                        recipe_id=recipe["id"],
                        title=recipe["title"],
                        servings=recipe.get("servings", 4),
                        prep_time_minutes=recipe.get("prep_time_minutes", 15),
                        cook_time_minutes=recipe.get("cook_time_minutes", 20),
                        tags=recipe.get("tags", []),
                        matched_vibes=list(set(matched_vibes_list)),
                        score=score,
                        ingredients=recipe.get("ingredients", []),
                        instructions=recipe.get("instructions", []),
                        allergen_report=allergen_rep,
                    )
                )

        results.sort(key=lambda r: -r.score)
        return results[:limit]
