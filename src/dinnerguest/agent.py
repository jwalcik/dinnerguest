"""Dinnerguest ADK Root Agent.

Defines the primary orchestration agent and exposes Phase 1 & Phase 2 deterministic
tools and on-hand recommendation capabilities for the ADK CLI and Web UI (`adk web`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env file from active directory or repo root if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
    # Also check repo root
    root_env = Path(__file__).resolve().parent.parent.parent / ".env"
    if root_env.exists():
        load_dotenv(root_env)
except ImportError:
    pass

from dinnerguest.tools.allergen_tool import AllergenChecker
from dinnerguest.tools.pantry_search_tool import PantrySearchTool
from dinnerguest.tools.scaler_tool import RecipeScaler


# Initialize tool instances
_allergen_checker = AllergenChecker()
_pantry_search_tool = PantrySearchTool(allergen_checker=_allergen_checker)
_recipe_scaler = RecipeScaler()


def find_recipes_by_on_hand_ingredients(
    ingredients: List[str],
    max_missing: int = 2,
    page: int = 1,
) -> Dict[str, Any]:
    """Finds recipes matching ingredients the user has on hand.

    Handles common culinary substitutions (e.g. shallot -> onion), distinguishes
    complete matches from partial matches, and suggests missing pantry staples.
    Always returns up to 5 recipes at a time.

    Args:
        ingredients: List of ingredient names the user currently has (e.g. ["ground beef", "tomatoes", "onion"]).
        max_missing: Maximum allowed missing ingredients in matching recipes (default 2).
        page: Page number for pagination (default 1, 5 recipes per page).

    Returns:
        A dictionary containing matching recipes, matched/substituted/missing ingredients,
        total match count N, has_more flag, and pantry staple follow-up questions.
    """
    result = _pantry_search_tool.search_recipes(
        on_hand_ingredients=ingredients,
        max_missing=max_missing,
        page=page,
        page_size=5,
    )
    return result.to_dict()


def check_allergens_for_ingredients(ingredients: List[str]) -> Dict[str, Any]:
    """Deterministically checks an ingredient list against FDA FALCPA Big 9 and extended allergens.

    Detects direct allergens (dairy, egg, peanuts, tree nuts, wheat, soy, fish, shellfish, sesame),
    compound derivatives (e.g. Worcestershire sauce -> fish, pesto -> nuts & dairy), and flags
    ambiguous ingredients requiring manual label verification.

    Args:
        ingredients: List of raw ingredient descriptions to inspect.

    Returns:
        Structured allergen report with detected allergens, safe flags, and guest advisory notices.
    """
    report = _allergen_checker.check_recipe_ingredients(ingredients)
    return report.to_dict()


def scale_recipe_servings(
    recipe_title: str,
    ingredients: List[str],
    original_servings: int,
    target_servings: int,
) -> Dict[str, Any]:
    """Scales a recipe's ingredient quantities up or down to match a target serving size.

    Validates physical culinary constraints:
    - Warns if scaling down yields less than 1/8 tsp of an ingredient and calculates the recommended minimum scale.
    - Warns if scaling up exceeds 15 lbs of weighed ingredients or 1 gallon of liquid, advising batch cooking.

    Args:
        recipe_title: Name of the recipe being scaled.
        ingredients: List of ingredient strings with quantities (e.g. ["1 cup flour", "0.5 tsp salt"]).
        original_servings: Number of servings the original recipe produces.
        target_servings: Desired number of servings to scale to.

    Returns:
        Scaled recipe quantities formatted as fractions, scale factor, and culinary warnings.
    """
    result = _recipe_scaler.scale_recipe(
        recipe_title=recipe_title,
        ingredients=ingredients,
        original_servings=original_servings,
        target_servings=target_servings,
    )
    return result.to_dict()


# ADK Agent Definition
DINNERGUEST_INSTRUCTION = """
You are Dinnerguest, an expert culinary orchestration assistant designed to facilitate meal and party planning.

Your primary responsibilities include:
1. Recommending recipes based on ingredients the user has on hand:
   - Call the `find_recipes_by_on_hand_ingredients` tool to find matching recipes.
   - Consider and explain common ingredient substitutions (such as using an onion in place of shallots or scallions).
   - If a recipe is missing 1 or 2 pantry staples, ask polite follow-up questions (e.g., "Do you happen to have any dry pasta on hand?").
   - Rule: Never provide more than 5 recipe suggestions at a time. If there are more than 5 matching recipes, present the top 5, clearly inform the user that there are a total of N recipes that match their request, and ask if they would like to explore the other options.
   - For every recommended recipe, communicate the allergen notice clearly so guests remain safe.

2. Recipe Scaling:
   - When a user asks to scale a recipe up or down (e.g., "This recipe serves 4, help me scale it to 12"), call `scale_recipe_servings`.
   - If the tool returns a warning that an ingredient drops below 1/8 tsp, advise the user that scaling down that far is difficult to measure and suggest the recommended minimum yield.
   - If the tool returns a bulk warning (over 15 lbs or over 1 gallon), inform the user that they will need large preparation vessels or should cook the recipe in batches.

3. Allergen Checks:
   - When asked about allergens in recipes or for a party, call `check_allergens_for_ingredients`.
   - Never guess or speculate on allergens without running the deterministic tool.
   - Relay any ambiguous ingredients (such as "natural flavors" or "spice blend") to the user as items requiring label verification.

Be warm, conversational, encouraging, and clear.
"""

# Attempt ADK import; provide fallback for environments without google-adk installed
try:
    from google.adk.agents.llm_agent import Agent
except ImportError:
    try:
        from google.adk.agents import Agent
    except ImportError:
        try:
            from google.adk import Agent
        except ImportError:
            # Fallback mock for local test environments without google-adk installed
            class Agent:  # type: ignore
                def __init__(self, **kwargs: Any):
                    for k, v in kwargs.items():
                        setattr(self, k, v)


root_agent = Agent(
    name="Dinnerguest",
    model=os.getenv("GEN_MODEL", "gemini-3.8-flash"),
    description="Culinary orchestrator assisting with meal planning, on-hand recipe discovery, scaling, and allergen checks.",
    instruction=DINNERGUEST_INSTRUCTION,
    tools=[
        find_recipes_by_on_hand_ingredients,
        check_allergens_for_ingredients,
        scale_recipe_servings,
    ],
)
