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
    "toasted", "dry", "dried", "medium", "large", "small", "raw", "canned", "whole",
    "crisp", "crispy", "thick", "thin", "fine", "finely", "coarsely", "optional"
}

# Cut, container, and unit descriptors that do not define the core ingredient identity
CUT_AND_CONTAINER_WORDS = {
    "can", "cans", "pkg", "package", "packages", "jar", "jars", "bottle", "bottles",
    "box", "boxes", "bag", "bags", "bunch", "bunches", "strip", "strips", "slice",
    "slices", "piece", "pieces", "half", "halves", "leaf", "leaves", "head", "heads",
    "stalk", "stalks", "sprig", "sprigs", "pat", "pats", "stick", "sticks", "cube",
    "cubes", "chunk", "chunks", "fillet", "fillets", "cutlet", "cutlets", "wedge",
    "wedges", "ring", "rings", "clove", "cloves"
}

# Processed or derivative product head words that fundamentally change a raw ingredient
# (e.g. 'tomato' != 'tomato soup', 'chicken' != 'chicken broth', 'apple' != 'apple cider vinegar')
DERIVATIVE_HEAD_WORDS = {
    "soup", "broth", "stock", "bouillon", "paste", "sauce", "ketchup", "juice",
    "vinegar", "syrup", "starch", "cornstarch", "oil", "meal", "cornmeal", "flour",
    "extract", "soda", "powder", "gravy", "jelly", "jam", "chip", "tortilla",
    "cereal", "wine", "cider", "mix"
}

# Universal kitchen basics assumed present in standard cooking inquiries
UNIVERSAL_KITCHEN_BASICS = {
    "salt", "sea salt", "kosher salt", "table salt", "black pepper", "pepper",
    "cracked black pepper", "ground black pepper", "freshly ground black pepper",
    "white pepper", "water", "warm water", "cold water", "boiling water", "ice",
    "cooking spray", "nonstick spray"
}


def stem_ingredient_word(word: str) -> str:
    """Singularizes and normalizes an individual ingredient token."""
    w = re.sub(r"[^\w-]", "", word.lower().strip())
    if len(w) <= 2:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("oes") and len(w) > 4:
        return w[:-2]
    if w.endswith("ves") and len(w) > 4:
        return w[:-3] + "f"
    if w.endswith("es") and any(w.endswith(s) for s in ("ches", "shes", "xes", "zes", "sses")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tokenize_ingredient_for_matching(phrase: str) -> Set[str]:
    """Converts a normalized ingredient phrase into a canonical set of stemmed identity tokens."""
    norm = normalize_ingredient_phrase(phrase)
    raw_words = [stem_ingredient_word(w) for w in norm.split() if w]
    tokens = {
        w for w in raw_words
        if w and w not in PREP_ADJECTIVES and w not in CUT_AND_CONTAINER_WORDS and not re.match(r"^[\d/.-]+$", w)
    }
    if not tokens and raw_words:
        tokens = {raw_words[-1]}

    # Normalize bread bun / sandwich roll equivalents (e.g. 'hamburger buns', 'brioche burger buns', 'sandwich rolls')
    bun_modifiers = {"hamburger", "burger", "sandwich", "brioche", "kaiser", "sesame", "seeded", "hoagie", "sub", "ciabatta"}
    if "roll" in tokens and (tokens & bun_modifiers):
        tokens.discard("roll")
        tokens.add("bun")
    if "bun" in tokens and len(tokens) > 1:
        stripped = tokens - bun_modifiers
        if stripped:
            tokens = stripped

    return tokens


def ingredients_match(user_phrase: str, recipe_phrase: str) -> bool:
    """Determines whether a user's on-hand ingredient satisfies a recipe ingredient requirement.

    Prevents false-positive substring matches (e.g. fresh 'tomato' matching 'condensed tomato soup',
    'chicken breasts' matching 'chicken broth', or 'black pepper' matching 'bell peppers') while
    supporting singular/plural variations and modifier subsets.
    """
    u_tokens = tokenize_ingredient_for_matching(user_phrase)
    r_tokens = tokenize_ingredient_for_matching(recipe_phrase)
    if not u_tokens or not r_tokens:
        return False

    # 1. Derivative product guard: if one side is a soup, broth, sauce, paste, powder, oil, vinegar, etc.
    # and the other side does not have that product word, they are distinct culinary items.
    sym_diff = u_tokens ^ r_tokens
    if sym_diff & DERIVATIVE_HEAD_WORDS:
        return False

    # 2. Compound identity guards for butter, milk, cream, and pepper (spice vs vegetable)
    if "butter" in (u_tokens | r_tokens):
        if "butter" not in (u_tokens & r_tokens):
            return False
        if sym_diff & {"peanut", "almond", "cashew", "apple", "cookie", "cocoa", "squash", "bean", "lettuce"}:
            return False

    if "milk" in (u_tokens | r_tokens):
        if "milk" not in (u_tokens & r_tokens):
            return False
        if sym_diff & {"coconut", "almond", "soy", "oat", "condensed", "evaporated", "buttermilk", "chocolate"}:
            return False

    if "cream" in (u_tokens | r_tokens):
        if "cream" not in (u_tokens & r_tokens):
            return False
        if sym_diff & {"sour", "cheese", "ice", "tartar", "wheat", "coconut", "corn", "soup"}:
            return False

    if "pepper" in (u_tokens & r_tokens):
        # Distinguish black/white peppercorn spice from bell/chili pepper vegetables
        if sym_diff & {"bell", "jalapeno", "poblano", "serrano", "habanero", "anaheim", "sweet", "stuffed"}:
            return False

    # 3. Exact token set equality or clean subset match
    if u_tokens == r_tokens:
        return True
    if u_tokens.issubset(r_tokens) or r_tokens.issubset(u_tokens):
        return True

    return False


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
    # Split on comma (e.g. "onion, diced"), but preserve subsequent segments if the first
    # segment is just a leading adjective before a comma (e.g. "4 boneless, skinless chicken breasts")
    unit_set = set(ALL_KNOWN_UNITS)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    kept_parts: List[str] = []
    for part in parts:
        kept_parts.append(part)
        part_words = [re.sub(r"[^\w/.-]", "", w) for w in part.split()]
        has_noun = any(
            w and not re.match(r"^[\d/.-]+$", w) and w not in unit_set and w not in PREP_ADJECTIVES and w not in CUT_AND_CONTAINER_WORDS and w != "of"
            for w in part_words
        )
        if has_noun:
            break
    main_part = " ".join(kept_parts)

    # Strip numbers, fractions, and units
    words = main_part.split()
    filtered_words = []

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
    unused_ingredients: List[str] = field(default_factory=list)
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
            "unused_ingredients": self.unused_ingredients,
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
        canonical_recipes = base_dir / "data" / "recipes_canonical.json"
        fixture_recipes = base_dir.parent.parent / "tests" / "fixtures" / "sample_recipes.json"
        default_recipes = canonical_recipes if canonical_recipes.exists() else fixture_recipes
        self.recipes_path = Path(recipes_path or default_recipes)
        self.substitutions_path = Path(substitutions_path or (base_dir / "data" / "substitutions_canonical.json"))
        self.staples_path = Path(staples_path or (base_dir / "data" / "pantry_staples.json"))
        self.allergen_checker = allergen_checker or AllergenChecker()

        self._load_data()

    def _load_data(self) -> None:
        canonical_list: List[Dict[str, Any]] = []
        if self.recipes_path.exists():
            try:
                with open(self.recipes_path, "r", encoding="utf-8") as f:
                    canonical_list = json.load(f)
            except Exception:
                canonical_list = []

        try:
            from dinnerguest.database import fetch_recipes_from_db
            db_recipes = fetch_recipes_from_db(limit=100)
            if db_recipes:
                by_id = {str(r["id"]): r for r in canonical_list}
                for r in db_recipes:
                    by_id[str(r["id"])] = r
                self.recipes: List[Dict[str, Any]] = list(by_id.values())
            else:
                self.recipes = canonical_list
        except Exception:
            self.recipes = canonical_list

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
            for key, info in self.substitutions.items():
                if ingredients_match(key, recipe_token):
                    sub_info = info
                    break

        if not sub_info:
            return None

        for candidate in sub_info.get("substitutes", []):
            candidate_norm = normalize_ingredient_phrase(candidate)
            for avail in available_tokens:
                if ingredients_match(avail, candidate_norm):
                    return SubstitutionSuggestion(
                        recipe_ingredient=recipe_token,
                        user_ingredient=avail,
                        ratio=sub_info.get("ratio", "1:1"),
                        note=sub_info.get("note", f"Can substitute {avail} for {recipe_token}"),
                    )
        return None

    def is_staple(self, token: str) -> bool:
        """Determines if an ingredient token is a common kitchen pantry staple.

        Includes universal staples (salt, pepper, oils, water, basic spices, baking essentials).
        """
        clean = token.lower().strip()
        if any(v in clean for v in ("bell pepper", "jalapeno", "poblano", "serrano", "habanero", "stuffed pepper")):
            return False
        if clean in self.staples:
            return True
        for staple in self.staples:
            if ingredients_match(staple, clean):
                return True
        # Catch-all dry spices, seasonings, extracts, and universal baking essentials
        spice_or_staple_patterns = [
            "spice", "seasoning", "extract", "powder", "oil", "salt", "pepper",
            "cinnamon", "oregano", "paprika", "cumin", "nutmeg", "thyme", "rosemary",
            "basil", "clove", "bay leaf", "vanilla", "vinegar", "sugar", "flour", "water"
        ]
        if any(p in clean for p in spice_or_staple_patterns):
            return True
        return False

    def search_recipes(
        self,
        on_hand_ingredients: List[str],
        category: Optional[str] = None,
        max_missing: int = 2,
        page: int = 1,
        page_size: int = 5,
        assume_basic_staples: bool = False,
    ) -> SearchResult:
        """Finds recipes matching on-hand ingredients, ranking by lowest missing items and highest coverage.

        Accounts for unused ingredients (e.g. user has apples, oats, brown sugar, and onion; finds apple crisp,
        excluding onion), supports category/meal-type filtering (e.g. 'dessert'), and leverages Cloud SQL
        vector RAG embeddings when available.
        """
        normalized_user_items_map = {}
        for i in on_hand_ingredients:
            clean_i = i.strip()
            if clean_i:
                normalized_user_items_map[normalize_ingredient_phrase(clean_i)] = clean_i
        normalized_user_items = set(normalized_user_items_map.keys())
        # Handle "dry spices" wildcard if user mentions dry spices / spices
        has_spices_wildcard = any("spice" in i or "seasoning" in i for i in normalized_user_items)

        # 1. Candidate Retrieval: Cloud SQL hybrid vector/SQL query or local dataset
        candidate_recipes = []
        try:
            from dinnerguest.database import fetch_pantry_candidates_from_db
            db_candidates = fetch_pantry_candidates_from_db(
                ingredients=on_hand_ingredients,
                category=category,
                limit=100,
            )
            if db_candidates:
                candidate_recipes = db_candidates
        except Exception:
            pass

        if not candidate_recipes:
            candidate_recipes = self.recipes

        matches: List[RecipeMatch] = []
        staple_followup_candidates: Dict[str, int] = {}

        for recipe in candidate_recipes:
            # Category / meal-type filtering
            if category:
                cat_lower = category.lower().strip()
                recipe_tags = {t.lower() for t in recipe.get("tags", [])}
                recipe_title = recipe.get("title", "").lower()
                title_words = set(re.findall(r"\b[a-z]+\b", recipe_title))

                savory_dish_words = {
                    "chicken", "beef", "pork", "bacon", "turkey", "ham", "sausage", "lamb",
                    "fish", "salmon", "tuna", "shrimp", "crab", "lobster", "clam", "steak",
                    "burger", "burgers", "sandwich", "sandwiches", "club", "wrap", "wraps",
                    "taco", "tacos", "fajita", "fajitas", "enchilada", "quesadilla", "burrito",
                    "skillet", "roast", "stew", "soup", "chowder", "chili", "pasta", "noodles",
                    "lasagna", "bolognese", "meatball", "meatballs", "pesto", "salad", "pizza",
                    "casserole", "shepherd", "dinner", "lunch", "entree", "savory"
                }
                dessert_words = {
                    "dessert", "sweet", "baking", "cookies", "cookie", "crisp", "cake",
                    "cupcake", "cupcakes", "pie", "crumble", "tart", "brownie", "brownies",
                    "fudge", "frosting", "icing", "candy", "pudding", "cobbler", "parfait"
                }
                has_savory_indicator = bool((recipe_tags | title_words) & savory_dish_words) or "pot pie" in recipe_title
                has_dessert_indicator = bool((recipe_tags | title_words) & dessert_words) or "ice cream" in recipe_title

                if cat_lower == "dessert":
                    if not has_dessert_indicator or has_savory_indicator:
                        continue
                elif cat_lower in ["soup", "stew", "chowder"]:
                    is_soup = bool((recipe_tags | title_words) & {"soup", "stew", "chowder", "chili", "broth", "bisque"})
                    if not is_soup:
                        continue
                elif cat_lower in ["dinner", "lunch", "supper", "savory", "main", "main course", "entree"]:
                    if has_dessert_indicator and not has_savory_indicator:
                        continue

            raw_ingredients: List[str] = recipe.get("ingredients", [])
            if not raw_ingredients:
                continue

            matched_ings: List[str] = []
            substituted_ings: List[SubstitutionSuggestion] = []
            missing_ings: List[str] = []
            missing_staples: List[str] = []
            used_user_keys: Set[str] = set()

            for raw_ing in raw_ingredients:
                norm_recipe_ing = normalize_ingredient_phrase(raw_ing)

                # Direct match check using stemmed token & derivative-aware matcher
                direct_match = False
                for user_key in normalized_user_items:
                    if ingredients_match(user_key, norm_recipe_ing):
                        direct_match = True
                        used_user_keys.add(user_key)
                        break

                # Wildcard check for spices (oregano, garlic powder, cumin, pepper, salt, cinnamon)
                if not direct_match and has_spices_wildcard:
                    is_veg_pepper = any(v in norm_recipe_ing for v in ["bell", "jalapeno", "poblano", "serrano", "habanero", "stuffed"])
                    if not is_veg_pepper and any(s in norm_recipe_ing for s in ["salt", "pepper", "oregano", "powder", "spice", "cumin", "cinnamon"]):
                        direct_match = True
                        for uk in normalized_user_items:
                            if "spice" in uk or "seasoning" in uk:
                                used_user_keys.add(uk)

                if direct_match:
                    matched_ings.append(raw_ing)
                    continue

                # Substitution check
                sub = self.check_substitution(norm_recipe_ing, normalized_user_items)
                if sub:
                    substituted_ings.append(sub)
                    used_user_keys.add(normalize_ingredient_phrase(sub.user_ingredient))
                    continue

                # Universal kitchen basics (salt, black pepper, water, cooking spray) when max_missing > 0
                if (assume_basic_staples or max_missing > 0) and norm_recipe_ing in UNIVERSAL_KITCHEN_BASICS:
                    continue

                # Missing ingredient check
                if assume_basic_staples and self.is_staple(norm_recipe_ing):
                    matched_ings.append(f"{raw_ing} (from assumed pantry staples)")
                    continue

                if self.is_staple(norm_recipe_ing):
                    missing_staples.append(norm_recipe_ing)

                missing_ings.append(raw_ing)

            # Enforce minimum matched user ingredients to prevent 1-ingredient false matches
            min_user_matches = 1
            if len(normalized_user_items) >= 2 and (not category or category.lower().strip() != "dessert"):
                min_user_matches = 2
            if len(used_user_keys) < min_user_matches:
                continue

            # Filter by tolerance (allowing up to 2 extra pantry staples when >= 3 user ingredients match)
            non_staple_missing_count = max(0, len(missing_ings) - len(missing_staples))
            if max_missing == 0:
                passes_tolerance = len(missing_ings) == 0
            else:
                passes_tolerance = (
                    len(missing_ings) <= max_missing
                    or (non_staple_missing_count <= max_missing and len(missing_ings) <= max_missing + 2 and len(used_user_keys) >= 3)
                )

            if passes_tolerance:
                total_items = len(raw_ingredients)
                covered_items = len(matched_ings) + len(substituted_ings)
                score = covered_items / total_items if total_items > 0 else 0.0

                # Calculate unused user ingredients
                unused_user_items = [
                    orig_item
                    for norm_key, orig_item in normalized_user_items_map.items()
                    if norm_key not in used_user_keys
                ]

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
                    unused_ingredients=unused_user_items,
                    allergen_report=allergen_rep,
                )
                matches.append(match_entry)

        # Sort: prioritize high user-ingredient overlap when user provides 5+ items, then lowest missing items, then most matched items
        num_user_items = len(normalized_user_items)
        high_overlap_cutoff = max(2, num_user_items - 2)
        has_high_overlap = num_user_items >= 5 and any(
            (num_user_items - len(m.unused_ingredients)) >= high_overlap_cutoff for m in matches
        )

        matches.sort(
            key=lambda m: (
                0 if (not has_high_overlap or (num_user_items - len(m.unused_ingredients)) >= high_overlap_cutoff) else 1,
                len(m.missing_ingredients),
                -(num_user_items - len(m.unused_ingredients)),
                -(len(m.matched_ingredients) + len(m.substituted_ingredients)),
                -m.match_score,
            )
        )

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
