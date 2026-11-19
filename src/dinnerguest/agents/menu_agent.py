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

MENU_AGENT_INSTRUCTION = """You are the Menu & Event Planning Sub-Agent for Dinnerguest.
Your role is to coordinate multi-course meal planning, guest serving calculations,
cross-recipe allergen safety, and grocery list generation for events and dinner parties.

Guidelines:
1. Help users design balanced multi-course menus (Appetizer, Main, Side, Dessert).
2. Save event menus using `save_event_menu` with their guest count and theme.
3. Automatically scale dishes to the guest count and report allergen safety.
4. When requested, generate consolidated grocery lists for the saved event menus.
"""


menu_agent = Agent(
    name="MenuAgent",
    model=os.getenv("GEN_MODEL", "gemini-3.8-flash"),
    description="Specialized sub-agent for multi-course meal planning, serving scaling, allergen safety, and event persistence.",
    instruction=MENU_AGENT_INSTRUCTION,
    tools=[],
)

