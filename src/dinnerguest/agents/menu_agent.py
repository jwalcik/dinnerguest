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
from typing import Any, Dict, List, Optional

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
