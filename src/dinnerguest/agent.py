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

from dinnerguest.agents.menu_agent import MenuPlanningAgent
from dinnerguest.observability import traced_tool_call
from dinnerguest.tools.allergen_tool import AllergenChecker
from dinnerguest.tools.grocery_tool import GroceryListAggregatorTool
from dinnerguest.tools.menu_store import EventMenuStore
from dinnerguest.tools.pantry_search_tool import PantrySearchTool
from dinnerguest.tools.scaler_tool import RecipeScaler
from dinnerguest.tools.vibe_search_tool import VibeSearchTool


# Initialize tool instances
_allergen_checker = AllergenChecker()
_pantry_search_tool = PantrySearchTool(allergen_checker=_allergen_checker)
_recipe_scaler = RecipeScaler()
_vibe_tool = VibeSearchTool(allergen_checker=_allergen_checker)
_grocery_tool = GroceryListAggregatorTool()
_menu_store = EventMenuStore(scaler=_recipe_scaler, allergen_checker=_allergen_checker)
_menu_agent = MenuPlanningAgent(
    menu_store=_menu_store,
    scaler=_recipe_scaler,
    allergen_checker=_allergen_checker,
    grocery_tool=_grocery_tool,
)


def find_recipes_by_on_hand_ingredients(
    ingredients: List[str],
    category: Optional[str] = None,
    max_missing: int = 2,
    page: int = 1,
) -> Dict[str, Any]:
    """Finds recipes matching actual food ingredients the user has on hand in their kitchen, fridge, or pantry.

    IMPORTANT: Only call this tool when the user provides a list of actual culinary/food ingredients
    they want to cook with (e.g. ["chicken breasts", "hamburger buns", "lettuce", "tomato", "bacon", "ranch dressing"]).
    NEVER call this tool for event/menu planning requests (e.g. "I'm having some friends over for game night")
    or guest-count updates (e.g. "It's going to be 12 guests, not 6").

    Handles common culinary substitutions (e.g. shallot -> onion, brown sugar -> granulated sugar),
    supports filtering by meal type or category (e.g. "dessert", "soup", "dinner"), accounts for when
    not all on-hand ingredients are needed in a recipe (e.g. making an apple dessert with apples, oats,
    and brown sugar while leaving onions unused), and suggests missing pantry staples.
    Always returns up to 5 recipes at a time.

    Args:
        ingredients: List of food ingredient names the user currently has (e.g. ["apples", "brown sugar", "oats", "onion"]).
        category: Optional meal course or type filter (e.g. "dessert", "soup", "dinner").
        max_missing: Maximum allowed missing ingredients in matching recipes (default 2).
        page: Page number for pagination (default 1, 5 recipes per page).

    Returns:
        A dictionary containing matching recipes, matched/substituted/missing ingredients,
        unused user ingredients, total match count N, has_more flag, and pantry staple follow-up questions.
    """
    with traced_tool_call(
        "find_recipes_by_on_hand_ingredients",
        "Search recipes matching on-hand pantry ingredients and evaluate substitutions",
        {"ingredients": ingredients, "category": category, "max_missing": max_missing, "page": page},
    ) as ctx:
        result = _pantry_search_tool.search_recipes(
            on_hand_ingredients=ingredients,
            category=category,
            max_missing=max_missing,
            page=page,
            page_size=5,
        )
        out = result.to_dict()
        from dinnerguest.model_router import _DEFAULT_MODEL_ROUTER

        routing_decision = _DEFAULT_MODEL_ROUTER.route_on_hand_turn(tool_result=out)
        out["routing_decision"] = routing_decision.to_dict()
        ctx["result_summary"] = {
            "total_matches_count": out.get("total_matches_count", 0),
            "routed_model": routing_decision.model,
        }
        return out


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
    with traced_tool_call(
        "check_allergens_for_ingredients",
        "Run deterministic FDA FALCPA Big 9 allergen screening on ingredients",
        {"ingredients": ingredients},
    ) as ctx:
        report = _allergen_checker.check_recipe_ingredients(ingredients)
        out = report.to_dict()
        ctx["result_summary"] = {"has_allergens": out.get("has_allergens"), "unique_allergens": out.get("unique_allergens")}
        return out


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
    with traced_tool_call(
        "scale_recipe_servings",
        f"Scale '{recipe_title}' from {original_servings} to {target_servings} servings",
        {"recipe_title": recipe_title, "original_servings": original_servings, "target_servings": target_servings},
    ) as ctx:
        result = _recipe_scaler.scale_recipe(
            recipe_title=recipe_title,
            ingredients=ingredients,
            original_servings=original_servings,
            target_servings=target_servings,
        )
        out = result.to_dict()
        ctx["result_summary"] = {"scale_factor": out.get("scale_factor"), "warning_count": len(out.get("warnings", []))}
        return out


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
    with traced_tool_call(
        "generate_grocery_list",
        "Aggregate and categorize ingredients across recipes into supermarket aisles",
        {"recipe_count": len(recipe_ingredient_lists)},
    ) as ctx:
        result = _grocery_tool.generate(recipe_ingredient_lists)
        out = result.to_dict()
        ctx["result_summary"] = {"item_count": len(out.get("items", []))}
        return out


def search_recipes_by_vibe(
    vibe_or_craving: str,
    flavor_profile: Optional[str] = None,
    format: Optional[str] = None,
    protein: Optional[str] = None,
    excluded_ingredients: Optional[List[str]] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Searches recipes based on mood, vibe, season, weather, course type, or specific flavor cravings.

    Use this tool for any mood/craving request (e.g., "I feel like a sweet noodle dish",
    "I feel like a savory appetizer that doesn't have any cheese", "spicy pork").
    When the user specifies ingredients to avoid (e.g., "doesn't have any cheese", "without nuts"),
    pass them in `excluded_ingredients` (e.g., `["cheese"]`) and keep `vibe_or_craving` focused on
    what they want (e.g., `"savory appetizer"`).

    Args:
        vibe_or_craving: Positive description of the craving, dish, or course (e.g. "savory appetizer", "sweet noodle", "spicy pork", "cozy cold weather").
        flavor_profile: Optional flavor profile filter ("savory", "sweet").
        format: Optional dish format filter ("noodle", "taco", "skillet", "cookies", "crisp").
        protein: Optional protein filter ("pork", "beef", "chicken").
        excluded_ingredients: Optional list of ingredients to exclude (e.g. ["cheese"], ["beef"]).
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

    with traced_tool_call(
        "search_recipes_by_vibe",
        f"Semantic vibe/recipe search for '{vibe_or_craving}'",
        {
            "vibe_or_craving": vibe_or_craving,
            "filters": filters,
            "excluded_ingredients": excluded_ingredients,
            "limit": limit,
        },
    ) as ctx:
        results = _vibe_tool.search_by_vibe(
            vibe_or_craving,
            filters=filters,
            limit=limit,
            excluded_ingredients=excluded_ingredients,
        )
        out = [r.to_dict() for r in results]
        ctx["result_summary"] = {"matched_count": len(out), "top_title": out[0]["title"] if out else None}
        return out


def save_event_menu(
    name: str,
    guest_count: int,
    dishes: List[Dict[str, Any]],
    theme: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Saves or updates a named multi-course event menu in persistent memory.

    Automatically scales every dish's ingredient amounts to the target guest count,
    computes an allergen safety analysis across all courses, and stores the menu
    so it can be retrieved, edited, or converted into a shopping list anytime.

    Args:
        name: Name of the event (e.g. "Graduation Party", "Cozy Autumn Dinner").
        guest_count: Number of guests expected.
        dishes: List of dish dicts, each with:
            - recipe_title (str): Title of the recipe
            - ingredients (List[str]): Ingredient list
            - original_servings (int, optional, defaults to 4): Base recipe yield
            - course (str, optional): "Appetizer", "Main", "Side", "Dessert", etc.
        theme: Optional description of the party vibe or occasion.
        notes: Optional host planning notes or guest dietary comments.

    Returns:
        Structured event plan summary with scaled dishes, consolidated allergens,
        and diet-safe course indicators.
    """
    with traced_tool_call(
        "save_event_menu",
        f"Save and scale event menu '{name}' for {guest_count} guests",
        {"name": name, "guest_count": guest_count, "dish_count": len(dishes)},
    ) as ctx:
        summary = _menu_agent.plan_and_save_event_menu(
            name=name,
            guest_count=guest_count,
            dishes=dishes,
            theme=theme,
            notes=notes,
            auto_scale=True,
        )
        out = summary.to_dict()
        ctx["result_summary"] = {"name": out.get("name"), "dish_count": out.get("dish_count")}
        return out


def get_event_menu(name_or_id: str) -> Dict[str, Any]:
    """Recalls a previously saved event menu from persistent memory by name.

    Args:
        name_or_id: Name or slug of the event (e.g. "Graduation Party" or "graduation-party").

    Returns:
        The saved event menu details, courses, scaled ingredients, and allergen summary.
    """
    with traced_tool_call(
        "get_event_menu",
        f"Recall saved event menu '{name_or_id}'",
        {"name_or_id": name_or_id},
    ) as ctx:
        summary = _menu_agent.get_event_menu(name_or_id)
        if not summary:
            ctx["result_summary"] = {"found": False}
            return {"error": f"No saved menu found for '{name_or_id}'."}
        out = summary.to_dict()
        ctx["result_summary"] = {"found": True, "name": out.get("name")}
        return out


def list_event_menus() -> List[Dict[str, Any]]:
    """Lists all saved event menus in persistent memory.

    Returns:
        A list of event summaries including event name, guest count, theme, dishes,
        and allergen profiles.
    """
    with traced_tool_call("list_event_menus", "List all saved event menus in persistent storage", {}) as ctx:
        menus = _menu_agent.list_saved_menus()
        ctx["result_summary"] = {"menu_count": len(menus)}
        return menus


def generate_grocery_list_for_menu(name_or_id: str) -> Dict[str, Any]:
    """Generates a consolidated shopping list for an entire saved event menu.

    Combines ingredients from all courses (scaled to the event guest count),
    merges duplicate ingredients, and categorizes by supermarket aisle.

    Args:
        name_or_id: Name or slug of the saved event menu.

    Returns:
        Aggregated grocery list grouped by supermarket aisle.
    """
    with traced_tool_call(
        "generate_grocery_list_for_menu",
        f"Generate consolidated supermarket grocery list for event '{name_or_id}'",
        {"name_or_id": name_or_id},
    ) as ctx:
        grocery_list = _menu_agent.generate_menu_grocery_list(name_or_id)
        if not grocery_list:
            ctx["result_summary"] = {"found": False}
            return {"error": f"No saved menu found for '{name_or_id}'."}
        out = grocery_list.to_dict()
        ctx["result_summary"] = {"found": True, "item_count": len(out.get("items", []))}
        return out


def update_event_menu_dish(
    name_or_id: str,
    action: str,
    dish_query: str = "",
    target_dish_or_course: Optional[str] = None,
    course: Optional[str] = None,
    new_guest_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Modifies an existing event menu in context (replace a dish, add a course, remove a dish, or scale guest count).

    When replacing or adding a dish, uses `search_by_vibe` to find a matching recipe for `dish_query`,
    updates the saved event menu, automatically scales the dish to the menu's guest count, and recomputes
    the consolidated allergen profile across all courses.

    Args:
        name_or_id: Name or ID of the saved event menu (e.g. "Thanksgiving Dinner").
        action: One of "replace", "add", "remove", or "scale".
        dish_query: Recipe search query for the new dish (e.g. "pecan pie", "deviled eggs", "fruit pie").
        target_dish_or_course: Dish title or course to replace or remove (e.g. "Apple Crisp" or "Dessert").
        course: Course label when adding or replacing a dish (e.g. "Appetizer", "Main", "Side", "Dessert").
        new_guest_count: Target guest count when action is "scale".

    Returns:
        Updated event plan summary, replaced/added recipe details, and updated allergen profile.
    """
    act = action.strip().lower()
    if act == "rename":
        new_label = (dish_query or target_dish_or_course or "").strip()
        if not new_label:
            return {"error": "No new name provided for rename."}
        summary = _menu_agent.rename_event_menu(name_or_id, new_label)
        if not summary:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        out = summary.to_dict()
        out["previous_name"] = name_or_id
        return out

    if act == "scale" and new_guest_count:
        summary = _menu_agent.scale_event_menu(name_or_id, int(new_guest_count))
        if not summary:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        return summary.to_dict()

    if act == "remove" and target_dish_or_course:
        res = _menu_agent.remove_dish_from_event_menu(name_or_id, target_dish_or_course)
        if not res:
            return {"error": f"Could not remove '{target_dish_or_course}' from '{name_or_id}'."}
        summary, removed_title = res
        out = summary.to_dict()
        out["removed_dish"] = removed_title
        return out

    # Search for the requested dish via VibeSearchTool
    candidates = _vibe_tool.search_by_vibe(dish_query, limit=5) if dish_query else []
    # Filter out any candidate that matches target_dish_or_course so we never replace a dish with itself
    if target_dish_or_course and candidates:
        target_words = [w.rstrip("s") for w in target_dish_or_course.lower().split() if len(w) >= 3]
        filtered = [
            c for c in candidates
            if not (target_words and all(w in c.title.lower() for w in target_words))
        ]
        if filtered:
            candidates = filtered

    if candidates:
        best = candidates[0]
        new_dish_dict = {
            "recipe_id": best.recipe_id,
            "recipe_title": best.title,
            "course": course or "Side",
            "original_servings": best.servings,
            "ingredients": best.ingredients,
        }
    else:
        new_dish_dict = {
            "recipe_id": "custom_dish",
            "recipe_title": dish_query.strip().title() or "Custom Dish",
            "course": course or "Side",
            "original_servings": 4,
            "ingredients": [dish_query.strip()] if dish_query.strip() else [],
        }

    if act == "replace":
        if not course:
            new_dish_dict["course"] = None  # Preserve replaced dish's course
        res = _menu_agent.replace_dish_in_event_menu(
            name_or_id=name_or_id,
            target_dish_or_course=target_dish_or_course or "",
            new_dish=new_dish_dict,
        )
        if not res:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        summary, replaced_title = res
        out = summary.to_dict()
        out["replaced_dish"] = replaced_title
        out["new_dish"] = candidates[0].to_dict() if candidates else new_dish_dict
        return out

    if act == "add":
        summary = _menu_agent.add_dish_to_event_menu(name_or_id=name_or_id, new_dish=new_dish_dict)
        if not summary:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        out = summary.to_dict()
        out["added_dish"] = candidates[0].to_dict() if candidates else new_dish_dict
        return out

    return {"error": f"Unsupported action '{action}'."}


# ADK Agent Definition
DINNERGUEST_INSTRUCTION = """
You are Dinnerguest, an expert culinary orchestration assistant designed to facilitate meal planning, recipe discovery, scaling, and allergen safety.

### Intent Matching & Sub-Agent Delegation
Carefully evaluate the user's semantic intent and multi-turn conversation context to match each request to the appropriate sub-agent (`MenuAgent`, `OnHandAgent`, `VibeAgent`) and tools:

1. Event, Party & Multi-Course Menu Planning (`MenuAgent` & Event Menu Tools):
   - Route to `MenuAgent` (and use `save_event_menu`, `update_event_menu_dish`, `get_event_menu`, `list_event_menus`, `generate_grocery_list_for_menu`) whenever the user wants to plan a menu or meal for an event, occasion, holiday, or gathering—including casual or social occasions such as game night, dinner parties, birthdays, potlucks, or hosting friends (e.g., "Help me plan a menu for game night", "I'm having some friends over for game night, help me plan a menu", "I need to plan a menu for an upcoming party", "I need to plan a dinner for 6", "I'd like to plan Thanksgiving Dinner").
   - Also route all multi-turn follow-ups about an active or saved menu here:
     * Providing or changing the guest count (e.g., "It's going to be 12 guests, not 6", "There will be 10 of us", "Scale the menu to 12 guests").
     * Specifying the number and types of courses/dishes to include.
     * Swapping or replacing a dish on the menu (e.g., "Replace the Apple Crisp with a pecan pie", "Let's replace the meatballs with some sort of grilled chicken dish").
     * Adding or removing a course/menu item (e.g., "Add a deviled egg appetizer", "Remove the stuffing").
     * Naming or renaming the event label for later recall (e.g., "Okay, let's call this event 'Saturday Gamenight'").
     * Recalling a previously planned menu or generating its consolidated shopping list.
   - Interactive Planning Rules for `MenuAgent`:
     * Unstated Guest Count: If a specific number of guests is NOT specified by the user, DO NOT assume a default (never assume 6 guests). Ask the user how many guests they are expecting as part of the planning process.
     * Courses & Dishes: The suggested traditional courses for major holidays (such as Thanksgiving or Easter) are fine to propose directly, but if a major holiday is not explicitly specified, ask the user about the number and types of dishes/courses that should be included in the plan.
     * Arbitrary Event Label: Allow the user to give the event an arbitrary name/label (or ask what they'd like to call it) so the menu can be saved and recalled later.
     * Iterative Modifications & Dietary Checks: When replacing or adding a dish via `update_event_menu_dish`, pass a clean culinary query (e.g., "grilled chicken", "pecan pie"). If the user notes a guest's dietary restriction (e.g., "doesn't eat beef"), check all remaining courses on the menu and proactively warn if another dish contains that ingredient.

2. On-Hand Pantry Ingredient Discovery (`OnHandAgent` & `find_recipes_by_on_hand_ingredients`):
   - Route to `OnHandAgent` and call `find_recipes_by_on_hand_ingredients` ONLY when the user provides a positive list of actual food ingredients they currently have on hand in their kitchen, fridge, or pantry and wants to know what they can make with them (e.g., "I have ground beef and hamburger buns, what can I make for dinner?", "I've got some chicken breasts, hamburger buns, lettuce, tomato, bacon, and ranch dressing. What can I make for dinner?", or "I have apples, brown sugar, oats, and an onion, is there a dessert I can make?").
   - CRITICAL DISTINCTION #1 (Negative Ingredient Exclusions vs. Pantry Ingredients): When a user describes a craving or dish they want along with an ingredient they want to AVOID—using phrases like "doesn't have any cheese", "don't have meat", "without dairy", or "no nuts" (e.g., "I feel like a savory appetizer that doesn't have any cheese")—they are expressing a **vibe/craving with an ingredient exclusion**, NOT listing cheese as an on-hand ingredient! Route these requests to `VibeAgent` and call `search_recipes_by_vibe(vibe_or_craving="savory appetizer", flavor_profile="savory", excluded_ingredients=["cheese"])`. Never call `find_recipes_by_on_hand_ingredients` for negated ingredients.
   - CRITICAL DISTINCTION #2 (Events & Guests vs. Pantry Ingredients): Conversational expressions about hosting people or adjusting event details (such as "I'm having some friends over for game night", "I need to plan a menu for game night", or "It's going to be 12 guests, not 6") are NOT pantry ingredient lists. Route those to `MenuAgent`.
   - When the user mentions a specific meal type or category (e.g. "dessert", "soup", "dinner"), pass it in the `category` argument.
   - A recipe does NOT need to use all of the user's ingredients. If a user has apples, brown sugar, oats, and an onion, recommend a dessert using the apples, brown sugar, and oats, and clearly note that the onion remains unused for another meal.
   - Prompt-Driven Pantry Staples & Ingredient Substitutions:
     * Never penalize recipes for missing basic kitchen staples unless the user specifically says they don't have them. Common staples to assume available include: cooking oil (olive/vegetable), water, salt, black pepper, basic baking staples (baking powder/soda, vanilla, flour, sugar), and common dry shelf spices (garlic powder, onion powder, cinnamon, oregano, cumin, paprika, etc.).
     * Use culinary judgment to recognize viable substitutions in context (e.g., brown sugar for granulated sugar, honey or maple syrup for sugar, shallots or scallions for onions, Greek yogurt for sour cream in baking, dried herbs for fresh herbs). Explain any substitution you suggest and why it works for that dish.
     * If an essential structural ingredient is missing and has no viable substitute (e.g., lacking ground beef in a meatloaf), do not suggest the recipe, or clearly highlight what specific key item the cook would need to acquire.
   - If a recipe is missing 1 or 2 secondary items, ask polite follow-up questions (e.g., "Do you happen to have any dry pasta or rice on hand?").
   - Rule: Never provide more than 5 recipe suggestions at a time. If there are more than 5 matching recipes, present the top 5, clearly inform the user that there are a total of N recipes that match their request, and ask if they would like to explore the other options.
   - For every recommended recipe, communicate the allergen notice clearly so guests remain safe.

3. Mood, Vibe, Craving, or Single-Dish Discovery (`VibeAgent` & `search_recipes_by_vibe`):
   - Route to `VibeAgent` and call `search_recipes_by_vibe` when a user asks for a standalone recipe or recommendation based on mood, flavor craving, course type, weather, or dietary/ingredient exclusions (e.g., "I feel like a sweet noodle dish", "I feel like a savory appetizer that doesn't have any cheese", "I would like a spicy pork dish", "What's something cozy I can cook?", "I need a recipe for chicken soup").
   - When the user specifies an ingredient exclusion (e.g., "doesn't have any cheese", "without mushrooms", "no dairy"), pass the excluded items in `excluded_ingredients` (e.g., `excluded_ingredients=["cheese"]`) and pass only the positive dish/craving description in `vibe_or_craving` (e.g., `"savory appetizer"`). Double-check that none of the recipes you present contain the excluded ingredient.
   - Ask clarifying follow-up questions to help narrow down broad cravings when needed:
     * For spicy pork: ask whether they would prefer a spicy taco recipe or a noodle dish.
     * For cozy / cold weather: ask whether they would like something savory or sweet.
   - Rule: Ask no more than five clarifying questions. Keep questions friendly, direct, and limited.
   - Call `search_recipes_by_vibe` once the user's preferences are clear or after narrowing down.
   - If unable to identify a specific recommendation within five questions, apologize politely for not being able to find a good option and ask the user to provide more details in their request.
   - For every recommended recipe, communicate the allergen notice clearly.

4. Standalone Recipe Scaling (`scale_recipe_servings`):
   - When a user asks to scale an individual recipe up or down (e.g., "This recipe serves 4, help me scale it to 12"), call `scale_recipe_servings`. (Note: If they are changing the guest count for an event menu being planned, route to `MenuAgent` / `update_event_menu_dish` instead.)
   - If the tool returns a warning that an ingredient drops below 1/8 tsp, advise the user that scaling down that far is difficult to measure and suggest the recommended minimum yield.
   - If the tool returns a bulk warning (over 15 lbs or over 1 gallon), inform the user that they will need large preparation vessels or should cook the recipe in batches.

5. Allergen Checks (`check_allergens_for_ingredients`):
   - When asked about allergens in ingredients or recipes, call `check_allergens_for_ingredients`.
   - Never guess or speculate on allergens without running the deterministic tool.
   - Relay any ambiguous ingredients (such as "natural flavors" or "spice blend") to the user as items requiring label verification.

6. Standalone Grocery List Generation (`generate_grocery_list`):
   - When a user has selected one or more standalone recipes and wants a shopping list, call `generate_grocery_list` (or `generate_grocery_list_for_menu` if shopping for a saved event menu).
   - Present the list grouped by supermarket aisle (Produce, Meat & Seafood, Dairy, Baking, Spices, etc.) and mention which recipes each combined ingredient comes from.

Formatting Guidelines:
- Always format all numbers, ingredient quantities, and culinary fractions in clean plain text (e.g., "1 1/2 cups", "1/2 tsp", "3/4 lb").
- Never use LaTeX math notation or dollar signs for fractions (do NOT write $\\frac{1}{2}$ or $1\\frac{1}{2}$).

Be warm, conversational, encouraging, and clear.
"""

from dinnerguest.adk import Agent
from dinnerguest.agents.menu_agent import menu_agent
from dinnerguest.agents.on_hand_agent import on_hand_agent
from dinnerguest.agents.vibe_agent import vibe_agent
from dinnerguest.memory import _DEFAULT_COMPACTOR


root_agent = Agent(
    name="Dinnerguest",
    model=os.getenv("GEN_MODEL", "gemini-3.8-flash"),
    description="Culinary orchestrator assisting with meal planning, on-hand recipe discovery, scaling, and allergen checks.",
    instruction=DINNERGUEST_INSTRUCTION,
    sub_agents=[
        vibe_agent,
        on_hand_agent,
        menu_agent,
    ],
    tools=[
        find_recipes_by_on_hand_ingredients,
        search_recipes_by_vibe,
        check_allergens_for_ingredients,
        scale_recipe_servings,
        generate_grocery_list,
        save_event_menu,
        get_event_menu,
        list_event_menus,
        generate_grocery_list_for_menu,
        update_event_menu_dish,
    ],
    before_model_callback=_DEFAULT_COMPACTOR.adk_before_model_compaction_callback,
)

