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

    def __post_init__(self) -> None:
        from dinnerguest.database import clean_recipe_text_list, clean_recipe_title
        self.title = clean_recipe_title(self.title)
        self.ingredients = clean_recipe_text_list(self.ingredients)
        self.instructions = clean_recipe_text_list(self.instructions)

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
        canonical_recipes = base_dir / "data" / "recipes_canonical.json"
        fixture_recipes = base_dir.parent.parent / "tests" / "fixtures" / "sample_recipes.json"
        default_recipes = canonical_recipes if canonical_recipes.exists() else fixture_recipes
        self.recipes_path = Path(recipes_path or default_recipes)
        self.taxonomy_path = Path(taxonomy_path or (base_dir / "data" / "vibe_taxonomy.json"))
        self.allergen_checker = allergen_checker or AllergenChecker()

        self._load_data()

    def _load_data(self) -> None:
        try:
            from dinnerguest.database import fetch_recipes_from_db
            db_recipes = fetch_recipes_from_db(limit=100)
            if db_recipes:
                self.recipes: List[Dict[str, Any]] = db_recipes
            elif self.recipes_path.exists():
                with open(self.recipes_path, "r", encoding="utf-8") as f:
                    self.recipes = json.load(f)
            else:
                self.recipes = []
        except Exception:
            if self.recipes_path.exists():
                with open(self.recipes_path, "r", encoding="utf-8") as f:
                    self.recipes = json.load(f)
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
        """Finds recipes matching mood, taste, or flavor profile.

        Prioritizes dense vector RAG via Cloud SQL pgvector when connected.
        Falls back to local JSON dataset with dynamic token relevance scoring
        only if Cloud SQL is absent or unreachable.
        """
        # 1. Primary path: Vector RAG search against PostgreSQL pgvector
        try:
            from dinnerguest.database import search_recipes_by_vector
            db_recipes = search_recipes_by_vector(query, limit=limit)
            if db_recipes is not None and len(db_recipes) > 0:
                results: List[VibeRecipeResult] = []
                for r in db_recipes:
                    allergen_rep = self.allergen_checker.check_recipe_ingredients(r.get("ingredients", []))
                    results.append(
                        VibeRecipeResult(
                            recipe_id=r["id"],
                            title=r["title"],
                            servings=r.get("servings", 4),
                            prep_time_minutes=r.get("prep_time_minutes", 15),
                            cook_time_minutes=r.get("cook_time_minutes", 20),
                            tags=r.get("tags", []),
                            matched_vibes=[query],
                            score=float(r.get("score", 1.0)),
                            ingredients=r.get("ingredients", []),
                            instructions=r.get("instructions", []),
                            allergen_report=allergen_rep,
                        )
                    )
                return results[:limit]
        except Exception:
            pass

        # 2. Fallback path: Local JSON dataset scoring via dynamic semantic token matching
        query_clean = query.lower()
        query_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", query_clean))
        matched_vibe_cats = self.extract_vibe_tokens(query_clean)

        filters = filters or {}
        required_format = filters.get("format", "").lower()
        required_profile = filters.get("flavor_profile", "").lower()

        # Extract substantive culinary query tokens (excluding generic stopwords)
        substantive_tokens = {
            w for w in query_words
            if w not in VIBE_STOP_WORDS and len(w) > 2
        }

        results = []

        for recipe in self.recipes:
            title_lower = recipe["title"].lower()
            recipe_tags = set(t.lower() for t in recipe.get("tags", []))
            recipe_ings = [i.lower() for i in recipe.get("ingredients", [])]
            recipe_insts = [ins.lower() for ins in recipe.get("instructions", [])]
            raw_text = f"{title_lower} {' '.join(recipe_tags)} {' '.join(recipe_ings)} {' '.join(recipe_insts)}"

            # Format constraint
            if required_format and required_format not in raw_text:
                continue

            # Flavor profile constraint
            if required_profile == "savory":
                is_sweet_dish = any(t in recipe_tags for t in ["sweet", "dessert", "baking", "cookies", "crisp"])
                if "savory" not in recipe_tags and "savory" not in raw_text and is_sweet_dish:
                    continue
            elif required_profile == "sweet":
                is_sweet_dish = any(t in recipe_tags for t in ["sweet", "dessert", "baking", "cookies", "crisp"]) or "sweet" in raw_text
                if not is_sweet_dish:
                    continue

            # If user queried substantive tokens (e.g. 'tuna', 'potato', 'soup'),
            # ensure recipe has at least one matching token or stem in its text
            if substantive_tokens:
                matches_any_substantive = False
                for token in substantive_tokens:
                    stem = token.rstrip("es").rstrip("s")
                    if stem in title_lower or stem in " ".join(recipe_tags) or stem in " ".join(recipe_ings):
                        matches_any_substantive = True
                        break
                # Only enforce substantive check if there were specific ingredient/dish keywords
                # and vibe category didn't match
                if not matches_any_substantive and not matched_vibe_cats:
                    continue

            # Compute dynamic relevance score
            score = 0.0
            matched_vibes_list: List[str] = []

            # 1. Exact query phrase in title bonus
            if query_clean in title_lower:
                score += 8.0
                matched_vibes_list.append(query_clean)

            # 2. Substantive word matches in title (weighted heavily)
            for w in substantive_tokens:
                stem = w.rstrip("es").rstrip("s")
                if stem in title_lower:
                    score += 5.0
                    matched_vibes_list.append(w)
                elif stem in " ".join(recipe_tags):
                    score += 3.5
                    matched_vibes_list.append(w)
                elif stem in " ".join(recipe_ings):
                    score += 2.5
                    matched_vibes_list.append(w)
                elif stem in raw_text:
                    score += 0.5

            # 3. Tag intersections
            for tag in recipe_tags:
                if tag in query_words or tag in matched_vibe_cats:
                    score += 2.5
                    matched_vibes_list.append(tag)

            # 4. Taxonomy category synonym matches
            for cat in matched_vibe_cats:
                cat_info = self.vibe_categories.get(cat, {})
                synonyms = set(cat_info.get("synonyms", []))
                if recipe_tags.intersection(synonyms):
                    score += 3.0
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
