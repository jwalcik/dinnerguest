"""Menu & Event Planning Sub-Agent.

Coordinates multi-course meal planning for parties and events:
- Course balance (Appetizer, Main, Side, Dessert).
- Multi-dish allergen cross-checking to ensure safe guest accommodations.
- Automatic serving size scaling to guest count.
- Consolidated grocery list generation from the full event menu.
- Long-term memory persistence (save, recall, update named event menus).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from dinnerguest.tools.allergen_tool import AllergenChecker, AllergenReport
from dinnerguest.tools.grocery_tool import GroceryList, GroceryListAggregatorTool
from dinnerguest.tools.menu_store import EventMenu, EventMenuStore, MenuDish
from dinnerguest.tools.scaler_tool import RecipeScaler


@dataclass
class MenuCourseRecommendation:
    course: str
    recipe_title: str
    servings: int
    allergens: List[str]


@dataclass
class EventPlanSummary:
    name: str
    guest_count: int
    theme: Optional[str]
    dishes: List[MenuDish]
    consolidated_allergens: List[str]
    allergen_free_courses: List[str]
    all_dishes_safe_for: Dict[str, bool]
    grocery_item_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "guest_count": self.guest_count,
            "theme": self.theme,
            "dish_count": len(self.dishes),
            "dishes": [d.to_dict() for d in self.dishes],
            "consolidated_allergens": self.consolidated_allergens,
            "allergen_free_courses": self.allergen_free_courses,
            "all_dishes_safe_for": self.all_dishes_safe_for,
            "grocery_item_count": self.grocery_item_count,
        }


class MenuPlanningAgent:
    """Agent orchestrating event menus, cross-recipe allergen safety, and persistent memory."""

    def __init__(
        self,
        menu_store: Optional[EventMenuStore] = None,
        scaler: Optional[RecipeScaler] = None,
        allergen_checker: Optional[AllergenChecker] = None,
        grocery_tool: Optional[GroceryListAggregatorTool] = None,
    ) -> None:
        self.scaler = scaler or RecipeScaler()
        self.allergen_checker = allergen_checker or AllergenChecker()
        self.menu_store = menu_store or EventMenuStore(
            scaler=self.scaler, allergen_checker=self.allergen_checker
        )
        self.grocery_tool = grocery_tool or GroceryListAggregatorTool()

    def plan_and_save_event_menu(
        self,
        name: str,
        guest_count: int,
        dishes: List[Dict[str, Any]],
        theme: Optional[str] = None,
        notes: Optional[str] = None,
        auto_scale: bool = True,
    ) -> EventPlanSummary:
        """Plans, scales, checks allergens, and saves a named event menu."""
        saved_menu = self.menu_store.save_menu(
            name=name,
            guest_count=guest_count,
            dishes=dishes,
            theme=theme,
            notes=notes,
            auto_scale=auto_scale,
        )

        return self._summarize_menu(saved_menu)

    def get_event_menu(self, name_or_id: str) -> Optional[EventPlanSummary]:
        """Retrieves and summarizes a saved event menu."""
        menu = self.menu_store.get_menu(name_or_id)
        if not menu:
            return None
        return self._summarize_menu(menu)

    def list_saved_menus(self) -> List[Dict[str, Any]]:
        """Lists all saved menus in storage."""
        return self.menu_store.list_menus()

    def generate_menu_grocery_list(self, name_or_id: str) -> Optional[GroceryList]:
        """Generates a consolidated shopping list for an entire saved event menu."""
        menu = self.menu_store.get_menu(name_or_id)
        if not menu:
            return None

        recipe_entries = []
        for dish in menu.dishes:
            recipe_entries.append({
                "title": dish.recipe_title,
                "ingredients": dish.scaled_ingredients or dish.original_ingredients,
                "scale_factor": 1.0,  # Already scaled to guest count
            })

        return self.grocery_tool.generate(recipe_entries)

    @staticmethod
    def _dish_to_input_dict(dish: MenuDish) -> Dict[str, Any]:
        return {
            "recipe_id": dish.recipe_id,
            "recipe_title": dish.recipe_title,
            "course": dish.course,
            "original_servings": dish.servings,
            "ingredients": dish.scaled_ingredients or dish.original_ingredients,
        }

    @staticmethod
    def _matches_target_dish(dish: MenuDish, target: str) -> bool:
        target_clean = target.strip().lower()
        if not target_clean:
            return False
        title_lower = dish.recipe_title.lower()
        course_lower = dish.course.lower()

        if target_clean == course_lower or (target_clean in {"starter", "appetizer"} and course_lower in {"starter", "appetizer"}):
            return True
        if target_clean in title_lower or title_lower in target_clean:
            return True

        target_words = [w.rstrip("s") for w in re.findall(r"\b[a-z]{3,}\b", target_clean) if w not in {"the", "our", "dish", "course", "recipe"}]
        if target_words and all(w in title_lower for w in target_words):
            return True
        if target_words and any(w in title_lower for w in target_words):
            return True
        return False

    def replace_dish_in_event_menu(
        self,
        name_or_id: str,
        target_dish_or_course: str,
        new_dish: Dict[str, Any],
    ) -> Optional[Tuple[EventPlanSummary, str]]:
        """Replaces a matching dish or course in an existing saved event menu and re-evaluates allergens/scaling."""
        menu = self.menu_store.get_menu(name_or_id)
        if not menu or not menu.dishes:
            return None

        match_idx = -1
        for idx, d in enumerate(menu.dishes):
            if self._matches_target_dish(d, target_dish_or_course):
                match_idx = idx
                break

        if match_idx == -1:
            # Default to replacing the dish with the same course as new_dish, or the last dish
            new_course = (new_dish.get("course") or "").lower()
            for idx, d in enumerate(menu.dishes):
                if new_course and d.course.lower() == new_course:
                    match_idx = idx
                    break
            if match_idx == -1:
                match_idx = len(menu.dishes) - 1

        replaced_title = menu.dishes[match_idx].recipe_title
        preserved_course = new_dish.get("course") or menu.dishes[match_idx].course

        updated_dishes: List[Dict[str, Any]] = []
        for idx, d in enumerate(menu.dishes):
            if idx == match_idx:
                updated_dishes.append({
                    "recipe_id": new_dish.get("recipe_id"),
                    "recipe_title": new_dish.get("recipe_title", "Custom Dish"),
                    "course": preserved_course,
                    "original_servings": new_dish.get("original_servings", 4),
                    "ingredients": new_dish.get("ingredients", []),
                })
            else:
                updated_dishes.append(self._dish_to_input_dict(d))

        summary = self.plan_and_save_event_menu(
            name=menu.name,
            guest_count=menu.guest_count,
            dishes=updated_dishes,
            theme=menu.theme,
            notes=menu.notes,
            auto_scale=True,
        )
        return summary, replaced_title

    def add_dish_to_event_menu(
        self,
        name_or_id: str,
        new_dish: Dict[str, Any],
    ) -> Optional[EventPlanSummary]:
        """Adds a new dish/course to an existing saved event menu in logical course order."""
        menu = self.menu_store.get_menu(name_or_id)
        if not menu:
            return None

        course_order = {"appetizer": 0, "starter": 0, "main": 1, "side": 2, "dessert": 3, "drink": 4}
        updated_dishes = [self._dish_to_input_dict(d) for d in menu.dishes]
        updated_dishes.append({
            "recipe_id": new_dish.get("recipe_id"),
            "recipe_title": new_dish.get("recipe_title", "Custom Dish"),
            "course": new_dish.get("course", "Side"),
            "original_servings": new_dish.get("original_servings", 4),
            "ingredients": new_dish.get("ingredients", []),
        })
        updated_dishes.sort(key=lambda d: course_order.get(str(d.get("course", "Side")).lower(), 2))

        return self.plan_and_save_event_menu(
            name=menu.name,
            guest_count=menu.guest_count,
            dishes=updated_dishes,
            theme=menu.theme,
            notes=menu.notes,
            auto_scale=True,
        )

    def remove_dish_from_event_menu(
        self,
        name_or_id: str,
        target_dish_or_course: str,
    ) -> Optional[Tuple[EventPlanSummary, str]]:
        """Removes a matching dish or course from an existing saved event menu."""
        menu = self.menu_store.get_menu(name_or_id)
        if not menu or not menu.dishes:
            return None

        removed_title: Optional[str] = None
        updated_dishes: List[Dict[str, Any]] = []
        for d in menu.dishes:
            if removed_title is None and self._matches_target_dish(d, target_dish_or_course):
                removed_title = d.recipe_title
                continue
            updated_dishes.append(self._dish_to_input_dict(d))

        if removed_title is None:
            return None

        summary = self.plan_and_save_event_menu(
            name=menu.name,
            guest_count=menu.guest_count,
            dishes=updated_dishes,
            theme=menu.theme,
            notes=menu.notes,
            auto_scale=True,
        )
        return summary, removed_title

    def scale_event_menu(
        self,
        name_or_id: str,
        new_guest_count: int,
    ) -> Optional[EventPlanSummary]:
        """Scales all dishes in an existing saved event menu to a new guest count."""
        menu = self.menu_store.get_menu(name_or_id)
        if not menu:
            return None

        updated_dishes = [self._dish_to_input_dict(d) for d in menu.dishes]
        return self.plan_and_save_event_menu(
            name=menu.name,
            guest_count=new_guest_count,
            dishes=updated_dishes,
            theme=menu.theme,
            notes=menu.notes,
            auto_scale=True,
        )

    def rename_event_menu(
        self,
        old_name_or_id: str,
        new_name: str,
    ) -> Optional[EventPlanSummary]:
        """Renames an existing saved event menu to a new custom label."""
        menu = self.menu_store.rename_menu(old_name_or_id, new_name)
        if not menu:
            return None
        return self._summarize_menu(menu)

    def _summarize_menu(self, menu: EventMenu) -> EventPlanSummary:
        """Calculates cross-dish dietary accommodations and course coverage."""
        all_allergens = set(menu.consolidated_allergens)

        # Check safety across common diets
        common_allergens = [
            "Milk & Dairy",
            "Egg",
            "Peanuts",
            "Tree Nuts",
            "Wheat & Gluten",
            "Soy",
            "Fish",
            "Crustacean Shellfish",
            "Sesame",
        ]
        safe_for: Dict[str, bool] = {
            allergen: (allergen not in all_allergens) for allergen in common_allergens
        }

        # Identify courses with zero detected allergens
        allergen_free_courses: List[str] = []
        for d in menu.dishes:
            if not d.allergens_detected:
                allergen_free_courses.append(f"{d.course}: {d.recipe_title}")

        # Compute grocery item count
        recipe_entries = [
            {
                "title": d.recipe_title,
                "ingredients": d.scaled_ingredients or d.original_ingredients,
            }
            for d in menu.dishes
        ]
        grocery_list = self.grocery_tool.generate(recipe_entries)

        return EventPlanSummary(
            name=menu.name,
            guest_count=menu.guest_count,
            theme=menu.theme,
            dishes=menu.dishes,
            consolidated_allergens=menu.consolidated_allergens,
            allergen_free_courses=allergen_free_courses,
            all_dishes_safe_for=safe_for,
            grocery_item_count=len(grocery_list.items),
        )


# ADK Agent Definition
import os
from dinnerguest.adk import Agent
from dinnerguest.tools.vibe_search_tool import VibeSearchTool

MENU_AGENT_INSTRUCTION = """You are the Menu & Event Planning Sub-Agent (MenuAgent) for Dinnerguest.
Your role is to coordinate multi-course meal and event planning, interactive gathering of event details,
guest serving scaling, cross-recipe allergen safety, persistent menu recall, and grocery list generation.

Guidelines:
1. Interactive Event Discovery (Never Assume Unstated Details):
   - Guest Count: If a specific number of guests is NOT specified by the user (e.g., "Help me plan a menu for game night", "I'm having some friends over for game night, help me plan a menu", or "I need to plan a menu for an upcoming party"), DO NOT assume a default guest count (never silently default to 6). Ask the user how many guests they are expecting as part of the planning process.
   - Courses & Dishes: The suggested traditional courses for major holidays (such as Thanksgiving Dinner or Easter Lunch) are fine to propose directly. However, if a major holiday is not explicitly specified and the user has not stated what courses/dishes they want, ask the user about the number and types of dishes or courses to include in the plan (e.g., Appetizer, Main, Side, Dessert, or game-night finger foods/snacks).
   - Arbitrary Event Name / Label: To save an event and recall its menu later, the event needs a name or label. Allow users to provide any arbitrary label (e.g., "Saturday Gamenight", "Game Night", "Book Club Dinner") rather than restricting them to pre-planned holiday names. If they haven't provided a label yet, ask what they would like to name/label the event or let them rename it at any time.

2. Building & Saving the Event Menu:
   - Use `search_recipes_by_vibe` to find recipes matching each requested course or dish type.
   - Once the guest count and courses/dishes are known, call `save_event_menu` with the event name/label, `guest_count`, and selected `dishes`. Every dish is automatically scaled to the target guest count and screened for FDA FALCPA Big 9 allergens.

3. Multi-Turn Menu Modifications & Guest Count Updates:
   - Maintain context across follow-up turns for the active event menu.
   - Updating Guest Count: If the user changes or clarifies the number of guests during planning or after a menu is created (e.g., "It's going to be 12 guests, not 6" or "Scale to 12 guests"), update the guest count for the event and call `update_event_menu_dish(action="scale", new_guest_count=12)` if a menu is already saved.
   - Swapping / Replacing a Dish: When the user asks to swap or replace a menu item (e.g., "Replace the Apple Crisp with a pecan pie" or "Let's replace the meatballs with some sort of grilled chicken dish"), call `update_event_menu_dish(action="replace", target_dish_or_course=..., dish_query=...)` using a clean culinary search query (e.g., "pecan pie", "grilled chicken").
   - Adding or Removing a Course: When the user asks to add an additional course/menu item (e.g., "Add a deviled egg appetizer") or remove one (e.g., "Remove the stuffing"), call `update_event_menu_dish` with `action="add"` or `action="remove"`.
   - Renaming the Event Label: When the user provides or changes the event's name/label (e.g., "Okay, let's call this event 'Saturday Gamenight'"), call `update_event_menu_dish(action="rename", dish_query="Saturday Gamenight")`.
   - Cross-Course Dietary Checks: If the user swaps a dish because a guest avoids a specific ingredient (e.g., "One of the folks coming doesn't eat beef"), proactively check the remaining courses on the menu and alert the user if any other planned dish also contains that ingredient.

4. Event Recall, Allergen Safety & Grocery Lists:
   - When the user asks to recall a previous event (e.g., "What did we plan for Saturday Gamenight?"), call `get_event_menu` or `list_event_menus`.
   - Always summarize the consolidated allergen profile across all courses and highlight which courses are allergen-free.
   - When requested, call `generate_grocery_list_for_menu` to produce a consolidated, aisle-organized shopping list for the entire scaled menu.
"""


def save_event_menu_adk(
    name: str,
    guest_count: int,
    dishes: List[Dict[str, Any]],
    theme: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    """Saves and scales a multi-course event menu to persistent storage."""
    agent = MenuPlanningAgent()
    return agent.plan_and_save_event_menu(
        name=name,
        guest_count=guest_count,
        dishes=dishes,
        theme=theme,
        notes=notes,
        auto_scale=True,
    ).to_dict()


def get_event_menu_adk(name_or_id: str) -> Dict[str, Any]:
    """Recalls a previously saved event menu by its name or label."""
    agent = MenuPlanningAgent()
    summary = agent.get_event_menu(name_or_id)
    if not summary:
        return {"error": f"No saved menu found for '{name_or_id}'."}
    return summary.to_dict()


def list_event_menus_adk() -> List[Dict[str, Any]]:
    """Lists all saved event menus in persistent storage."""
    agent = MenuPlanningAgent()
    return agent.list_saved_menus()


def generate_grocery_list_for_menu_adk(name_or_id: str) -> Dict[str, Any]:
    """Generates a consolidated, aisle-grouped shopping list for a saved event menu."""
    agent = MenuPlanningAgent()
    glist = agent.generate_menu_grocery_list(name_or_id)
    if not glist:
        return {"error": f"No saved menu found for '{name_or_id}'."}
    return glist.to_dict()


def update_event_menu_dish_adk(
    name_or_id: str,
    action: str,
    dish_query: str = "",
    target_dish_or_course: Optional[str] = None,
    course: Optional[str] = None,
    new_guest_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Modifies an existing event menu (replace a dish, add a course, remove a dish, scale guest count, or rename event)."""
    agent = MenuPlanningAgent()
    vibe_tool = VibeSearchTool()
    act = action.strip().lower()

    if act == "rename":
        new_label = (dish_query or target_dish_or_course or "").strip()
        if not new_label:
            return {"error": "No new name provided for rename."}
        summary = agent.rename_event_menu(name_or_id, new_label)
        if not summary:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        out = summary.to_dict()
        out["previous_name"] = name_or_id
        return out

    if act == "scale" and new_guest_count:
        summary = agent.scale_event_menu(name_or_id, int(new_guest_count))
        if not summary:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        return summary.to_dict()

    if act == "remove" and target_dish_or_course:
        res = agent.remove_dish_from_event_menu(name_or_id, target_dish_or_course)
        if not res:
            return {"error": f"Could not remove '{target_dish_or_course}' from '{name_or_id}'."}
        summary, removed_title = res
        out = summary.to_dict()
        out["removed_dish"] = removed_title
        return out

    candidates = vibe_tool.search_by_vibe(dish_query, limit=5) if dish_query else []
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
            new_dish_dict["course"] = None
        res = agent.replace_dish_in_event_menu(
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
        summary = agent.add_dish_to_event_menu(name_or_id=name_or_id, new_dish=new_dish_dict)
        if not summary:
            return {"error": f"No saved menu found for '{name_or_id}'."}
        out = summary.to_dict()
        out["added_dish"] = candidates[0].to_dict() if candidates else new_dish_dict
        return out

    return {"error": f"Unsupported action '{action}'."}


menu_agent = Agent(
    name="MenuAgent",
    model=os.getenv("GEN_MODEL", "gemini-3.8-flash"),
    description=(
        "Specialized sub-agent for planning menus for events, parties, holidays, game nights, and group meals. "
        "Handles interactive discovery of guest count, course structure, and custom event labels; "
        "modifying menus (replacing/adding/removing dishes, changing guest counts, renaming events); "
        "recalling saved menus; and generating event grocery lists."
    ),
    instruction=MENU_AGENT_INSTRUCTION,
    tools=[
        save_event_menu_adk,
        get_event_menu_adk,
        list_event_menus_adk,
        generate_grocery_list_for_menu_adk,
        update_event_menu_dish_adk,
    ],
)

