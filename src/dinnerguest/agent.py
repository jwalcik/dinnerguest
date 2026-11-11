"""Dinnerguest ADK Root Agent.

Defines the primary orchestration agent and exposes Phase 1–4 deterministic
tools and recommendation capabilities for the ADK CLI and Web UI (`adk web`).
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

# Ensure default to global location for Vertex AI preview models if not explicitly set
if "GOOGLE_CLOUD_LOCATION" not in os.environ:
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

from dinnerguest.tools.allergen_tool import AllergenChecker
from dinnerguest.tools.grocery_tool import GroceryListAggregatorTool
from dinnerguest.tools.pantry_search_tool import PantrySearchTool
from dinnerguest.tools.scaler_tool import RecipeScaler
from dinnerguest.tools.vibe_search_tool import VibeSearchTool


# Initialize tool instances
_allergen_checker = AllergenChecker()
_pantry_search_tool = PantrySearchTool(allergen_checker=_allergen_checker)
_recipe_scaler = RecipeScaler()
_vibe_tool = VibeSearchTool(allergen_checker=_allergen_checker)
_grocery_tool = GroceryListAggregatorTool()


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


def generate_grocery_list(
    recipe_ingredient_lists: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Generates a consolidated shopping list from one or more recipes' ingredient lists.

    Combines identical ingredients across recipes (e.g. 1 cup sugar from cookies +
    0.5 cup sugar from crisp = 1.5 cups sugar), converts quantities to the most
    readable culinary unit, and categorises every item by supermarket aisle
    (Produce, Meat & Seafood, Dairy & Eggs, Baking, etc.) for efficient shopping.

    Args:
        recipe_ingredient_lists: List of recipe dicts, each containing:
            - title (str): recipe name
            - ingredients (List[str]): raw ingredient strings with quantities
            - scale_factor (float, optional): multiplier if the recipe is being scaled

    Returns:
        Shopping list with combined quantities, aisle groupings, source recipe attribution,
        and total item count.
    """
    result = _grocery_tool.generate(recipe_ingredient_lists)
    return result.to_dict()


def search_recipes_by_vibe(
    vibe_or_craving: str,
    flavor_profile: Optional[str] = None,
    format: Optional[str] = None,
    protein: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Searches recipes based on mood, vibe, season, weather, or specific flavor cravings.

    Args:
        vibe_or_craving: Description of the craving or mood (e.g. "spicy pork", "cozy cold weather", "sweet dessert").
        flavor_profile: Optional flavor profile filter ("savory", "sweet").
        format: Optional dish format filter ("noodle", "taco", "skillet", "cookies", "crisp").
        protein: Optional protein filter ("pork", "beef", "chicken").
        limit: Max number of recipes to return (default 5).

    Returns:
        List of matching recipes with tags, matched vibes, instructions, and allergen screening.
    """
    filters = {}
    if flavor_profile:
        filters["flavor_profile"] = flavor_profile
    if format:
        filters["format"] = format
    if protein:
        filters["protein"] = protein

    results = _vibe_tool.search_by_vibe(vibe_or_craving, filters=filters, limit=limit)
    return [r.to_dict() for r in results]


# ADK Agent Definition
DINNERGUEST_INSTRUCTION = """
You are Dinnerguest, an expert culinary orchestration assistant designed to facilitate meal and party planning.

Your primary responsibilities include:
1. Recommending recipes based on mood, vibe, cravings, or weather:
   - When a user asks for a recommendation based on mood or flavor (e.g., "I would like a spicy pork dish" or "What's something cozy I can cook?"):
   - Ask clarifying follow-up questions to help narrow down choices:
     - For spicy pork: ask whether they would prefer a spicy taco recipe or a noodle dish.
     - For cozy / cold weather: ask whether they would like something savory or sweet.
   - Rule: Ask no more than five clarifying questions. Keep questions friendly, direct, and limited.
   - Call `search_recipes_by_vibe` once the user's preferences are clear or after narrowing down.
   - If unable to identify a specific recommendation within five questions, apologize politely for not being able to find a good option and ask the user to provide more details in their request.
   - For every recommended recipe, communicate the allergen notice clearly.

2. Recommending recipes based on ingredients the user has on hand:
   - Call the `find_recipes_by_on_hand_ingredients` tool to find matching recipes.
   - Consider and explain common ingredient substitutions (such as using an onion in place of shallots or scallions).
   - If a recipe is missing 1 or 2 pantry staples, ask polite follow-up questions (e.g., "Do you happen to have any dry pasta on hand?").
   - Rule: Never provide more than 5 recipe suggestions at a time. If there are more than 5 matching recipes, present the top 5, clearly inform the user that there are a total of N recipes that match their request, and ask if they would like to explore the other options.
   - For every recommended recipe, communicate the allergen notice clearly so guests remain safe.

3. Recipe Scaling:
   - When a user asks to scale a recipe up or down (e.g., "This recipe serves 4, help me scale it to 12"), call `scale_recipe_servings`.
   - If the tool returns a warning that an ingredient drops below 1/8 tsp, advise the user that scaling down that far is difficult to measure and suggest the recommended minimum yield.
   - If the tool returns a bulk warning (over 15 lbs or over 1 gallon), inform the user that they will need large preparation vessels or should cook the recipe in batches.

4. Allergen Checks:
   - When asked about allergens in recipes or for a party, call `check_allergens_for_ingredients`.
   - Never guess or speculate on allergens without running the deterministic tool.
   - Relay any ambiguous ingredients (such as "natural flavors" or "spice blend") to the user as items requiring label verification.

5. Grocery List Generation:
   - When a user has selected one or more recipes and wants a shopping list, call `generate_grocery_list`.
   - The tool automatically combines identical ingredients across multiple recipes (e.g. 1 cup sugar + 0.5 cup sugar = 1.5 cups sugar).
   - Present the list grouped by supermarket aisle (Produce, Meat & Seafood, Dairy, Baking, Spices, etc.) for efficient shopping.
   - If a recipe was scaled, include the scale_factor in the recipe entry so quantities reflect the correct serving size.
   - Mention which recipes each ingredient comes from when it was combined.

Formatting Guidelines:
- Always format all numbers, ingredient quantities, and culinary fractions in clean plain text (e.g., "1 1/2 cups", "1/2 tsp", "3/4 lb").
- Never use LaTeX math notation or dollar signs for fractions (do NOT write $\\frac{1}{2}$ or $1\\frac{1}{2}$).

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
        search_recipes_by_vibe,
        check_allergens_for_ingredients,
        scale_recipe_servings,
        generate_grocery_list,
    ],
)
