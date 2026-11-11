"""Event Menu Store & Persistent Memory.

Provides persistent storage and CRUD capabilities for named event menus
(e.g., 'Graduation Party', 'Cozy Sunday Dinner'), integrating deterministic
scaling to guest count and cross-dish allergen aggregation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dinnerguest.tools.allergen_tool import AllergenChecker
from dinnerguest.tools.scaler_tool import RecipeScaler


def _slugify(text: str) -> str:
    """Converts display name into a URL/file safe identifier."""
    clean = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[-\s]+", "-", clean)


@dataclass
class MenuDish:
    """A single dish planned within an event menu."""
    recipe_title: str
    servings: int
    course: str = "Main"  # "Appetizer", "Main", "Side", "Dessert", "Drink"
    recipe_id: Optional[str] = None
    original_ingredients: List[str] = field(default_factory=list)
    scaled_ingredients: List[str] = field(default_factory=list)
    allergens_detected: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recipe_title": self.recipe_title,
            "recipe_id": self.recipe_id,
            "course": self.course,
            "servings": self.servings,
            "original_ingredients": self.original_ingredients,
            "scaled_ingredients": self.scaled_ingredients,
            "allergens_detected": self.allergens_detected,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MenuDish:
        return cls(
            recipe_title=data.get("recipe_title", "Untitled Dish"),
            recipe_id=data.get("recipe_id"),
            course=data.get("course", "Main"),
            servings=data.get("servings", 4),
            original_ingredients=data.get("original_ingredients", []),
            scaled_ingredients=data.get("scaled_ingredients", []),
            allergens_detected=data.get("allergens_detected", []),
        )


@dataclass
class EventMenu:
    """Complete multi-course menu for a planned party or meal event."""
    name: str
    guest_count: int
    event_id: str = ""
    theme: Optional[str] = None
    notes: Optional[str] = None
    dishes: List[MenuDish] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    consolidated_allergens: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = _slugify(self.name)
        now_iso = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now_iso
        if not self.updated_at:
            self.updated_at = now_iso

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "name": self.name,
            "guest_count": self.guest_count,
            "theme": self.theme,
            "notes": self.notes,
            "dishes": [d.to_dict() for d in self.dishes],
            "dish_count": len(self.dishes),
            "consolidated_allergens": self.consolidated_allergens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EventMenu:
        dishes = [MenuDish.from_dict(d) for d in data.get("dishes", [])]
        return cls(
            name=data.get("name", "Untitled Event"),
            guest_count=data.get("guest_count", 4),
            event_id=data.get("event_id", ""),
            theme=data.get("theme"),
            notes=data.get("notes"),
            dishes=dishes,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            consolidated_allergens=data.get("consolidated_allergens", []),
        )


class EventMenuStore:
    """Persistent storage for named event menus.

    Supports saving, retrieving, listing, and updating menus.
    Automatically scales dishes to the menu's guest count and aggregates
    allergen profiles across all included dishes.
    """

    def __init__(
        self,
        storage_path: Optional[str | Path] = None,
        scaler: Optional[RecipeScaler] = None,
        allergen_checker: Optional[AllergenChecker] = None,
    ) -> None:
        if storage_path:
            self.storage_path = Path(storage_path)
        else:
            base_dir = Path(__file__).resolve().parent.parent.parent.parent
            self.storage_path = base_dir / "data" / "saved_menus.json"

        self.scaler = scaler or RecipeScaler()
        self.allergen_checker = allergen_checker or AllergenChecker()
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Initializes storage file if it does not exist."""
        if not self.storage_path.exists():
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._save_all({})

    def _load_all(self) -> Dict[str, Dict[str, Any]]:
        """Loads all raw menu dicts keyed by event_id."""
        if not self.storage_path.exists():
            return {}
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_all(self, data: Dict[str, Dict[str, Any]]) -> None:
        """Persists all menu dicts to JSON file."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def save_menu(
        self,
        name: str,
        guest_count: int,
        dishes: List[Dict[str, Any]],
        theme: Optional[str] = None,
        notes: Optional[str] = None,
        auto_scale: bool = True,
    ) -> EventMenu:
        """Saves or updates a named event menu.

        Args:
            name: Human-friendly event name (e.g. "Graduation Party").
            guest_count: Number of guests attending.
            dishes: List of dish dicts, each containing:
                - recipe_title (str)
                - ingredients (List[str])
                - original_servings (int, optional, defaults to 4)
                - course (str, optional, defaults to "Main")
                - recipe_id (str, optional)
            theme: Optional event theme description.
            notes: Optional general planning notes.
            auto_scale: Whether to scale dish ingredient amounts to guest_count.

        Returns:
            The saved `EventMenu` instance.
        """
        all_menus = self._load_all()
        event_id = _slugify(name)
        existing_raw = all_menus.get(event_id)

        menu_dishes: List[MenuDish] = []
        all_allergens_set = set()

        for d in dishes:
            title = d.get("recipe_title", "Untitled Dish")
            ings = d.get("ingredients", [])
            course = d.get("course", "Main")
            orig_servings = d.get("original_servings", 4)
            recipe_id = d.get("recipe_id")

            # Scale if requested and different from guest count
            if auto_scale and orig_servings > 0 and orig_servings != guest_count and ings:
                scale_res = self.scaler.scale_recipe(
                    recipe_title=title,
                    ingredients=ings,
                    original_servings=orig_servings,
                    target_servings=guest_count,
                )
                scaled_ings = [i.scaled_text for i in scale_res.scaled_ingredients]
                dish_servings = guest_count
            else:
                scaled_ings = list(ings)
                dish_servings = orig_servings

            # Deterministic allergen check
            allergen_rep = self.allergen_checker.check_recipe_ingredients(ings)
            dish_allergens = sorted(list(allergen_rep.unique_allergens))
            all_allergens_set.update(dish_allergens)

            menu_dishes.append(
                MenuDish(
                    recipe_title=title,
                    recipe_id=recipe_id,
                    course=course,
                    servings=dish_servings,
                    original_ingredients=ings,
                    scaled_ingredients=scaled_ings,
                    allergens_detected=dish_allergens,
                )
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        created_at = existing_raw.get("created_at", now_iso) if existing_raw else now_iso

        menu = EventMenu(
            name=name,
            guest_count=guest_count,
            event_id=event_id,
            theme=theme,
            notes=notes,
            dishes=menu_dishes,
            created_at=created_at,
            updated_at=now_iso,
            consolidated_allergens=sorted(list(all_allergens_set)),
        )

        all_menus[event_id] = menu.to_dict()
        self._save_all(all_menus)
        return menu

    def get_menu(self, name_or_id: str) -> Optional[EventMenu]:
        """Retrieves a menu by exact name or slugified event_id."""
        all_menus = self._load_all()
        target_id = _slugify(name_or_id)

        # Check direct slug match
        if target_id in all_menus:
            return EventMenu.from_dict(all_menus[target_id])

        # Check name case-insensitive
        lower_query = name_or_id.strip().lower()
        for menu_dict in all_menus.values():
            if menu_dict.get("name", "").lower() == lower_query:
                return EventMenu.from_dict(menu_dict)

        return None

    def list_menus(self) -> List[Dict[str, Any]]:
        """Lists summaries of all saved event menus."""
        all_menus = self._load_all()
        summaries: List[Dict[str, Any]] = []
        for m in all_menus.values():
            summaries.append({
                "event_id": m.get("event_id"),
                "name": m.get("name"),
                "guest_count": m.get("guest_count"),
                "theme": m.get("theme"),
                "dish_count": len(m.get("dishes", [])),
                "dishes": [d.get("recipe_title") for d in m.get("dishes", [])],
                "consolidated_allergens": m.get("consolidated_allergens", []),
                "updated_at": m.get("updated_at"),
            })
        summaries.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return summaries

    def delete_menu(self, name_or_id: str) -> bool:
        """Deletes a saved menu. Returns True if deleted, False if not found."""
        all_menus = self._load_all()
        target_id = _slugify(name_or_id)

        if target_id in all_menus:
            del all_menus[target_id]
            self._save_all(all_menus)
            return True

        lower_query = name_or_id.strip().lower()
        for k, v in list(all_menus.items()):
            if v.get("name", "").lower() == lower_query:
                del all_menus[k]
                self._save_all(all_menus)
                return True

        return False
