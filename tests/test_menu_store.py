"""Tests for EventMenuStore persistent storage and scaling."""

import tempfile
import unittest
from pathlib import Path

from dinnerguest.tools.menu_store import EventMenuStore


class TestEventMenuStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "test_menus.json"
        self.store = EventMenuStore(storage_path=self.storage_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_get_menu(self):
        """Should save a menu and retrieve it with all fields intact."""
        dishes = [
            {
                "recipe_title": "Classic Italian Meatballs",
                "course": "Main",
                "original_servings": 4,
                "ingredients": [
                    "1 lb ground beef",
                    "0.5 cup breadcrumbs",
                    "1 large egg",
                    "0.25 cup grated parmesan cheese",
                ],
            },
            {
                "recipe_title": "Bakery Style Chocolate Chip Cookies",
                "course": "Dessert",
                "original_servings": 12,
                "ingredients": [
                    "2.25 cups all-purpose flour",
                    "1 cup unsalted butter, softened",
                    "2 large eggs",
                ],
            },
        ]

        menu = self.store.save_menu(
            name="Graduation Party",
            guest_count=8,
            dishes=dishes,
            theme="Outdoor summer celebration",
            notes="Grandma is allergic to nuts",
        )

        self.assertEqual(menu.name, "Graduation Party")
        self.assertEqual(menu.event_id, "graduation-party")
        self.assertEqual(menu.guest_count, 8)
        self.assertEqual(len(menu.dishes), 2)

        # Retrieve by slug
        retrieved = self.store.get_menu("graduation-party")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "Graduation Party")
        self.assertEqual(retrieved.guest_count, 8)

        # Retrieve by exact name
        retrieved2 = self.store.get_menu("Graduation Party")
        self.assertIsNotNone(retrieved2)
        self.assertEqual(retrieved2.event_id, "graduation-party")

    def test_auto_scaling_to_guest_count(self):
        """Dish ingredients should be scaled to the event's guest count."""
        dishes = [
            {
                "recipe_title": "Meatballs",
                "original_servings": 4,
                "ingredients": ["1 lb ground beef", "0.5 cup breadcrumbs"],
            }
        ]
        # Guest count is 8 (double the original 4)
        menu = self.store.save_menu(
            name="Dinner for Eight",
            guest_count=8,
            dishes=dishes,
            auto_scale=True,
        )

        scaled = menu.dishes[0].scaled_ingredients
        self.assertTrue(any("2 lbs ground beef" in i or "2 lb ground beef" in i for i in scaled))
        self.assertTrue(any("1 cup breadcrumbs" in i for i in scaled))

    def test_consolidated_allergens_across_dishes(self):
        """Consolidated allergen list should merge allergens from all dishes."""
        dishes = [
            {
                "recipe_title": "Meatballs",
                "ingredients": ["1 lb ground beef", "1 large egg", "0.25 cup grated parmesan cheese"],
            },
            {
                "recipe_title": "Burgers",
                "ingredients": ["1 lb ground beef", "1 tbsp worcestershire sauce"],
            },
        ]
        menu = self.store.save_menu(
            name="Summer Cookout",
            guest_count=6,
            dishes=dishes,
        )

        allergens = menu.consolidated_allergens
        self.assertIn("Eggs", allergens)
        self.assertIn("Milk & Dairy", allergens)
        self.assertIn("Fish", allergens)  # From worcestershire

    def test_list_menus(self):
        """Should list summary metadata for all saved menus."""
        self.store.save_menu(
            name="Event One",
            guest_count=4,
            dishes=[{"recipe_title": "Dish A", "ingredients": ["1 cup flour"]}],
        )
        self.store.save_menu(
            name="Event Two",
            guest_count=10,
            dishes=[{"recipe_title": "Dish B", "ingredients": ["2 eggs"]}],
        )

        summaries = self.store.list_menus()
        self.assertEqual(len(summaries), 2)
        names = [s["name"] for s in summaries]
        self.assertIn("Event One", names)
        self.assertIn("Event Two", names)

    def test_delete_menu(self):
        """Should delete a menu and return True, or False if non-existent."""
        self.store.save_menu(
            name="To Delete",
            guest_count=2,
            dishes=[{"recipe_title": "Dish", "ingredients": ["1 apple"]}],
        )
        self.assertIsNotNone(self.store.get_menu("To Delete"))

        deleted = self.store.delete_menu("To Delete")
        self.assertTrue(deleted)
        self.assertIsNone(self.store.get_menu("To Delete"))

        # Deleting non-existent
        self.assertFalse(self.store.delete_menu("Non Existent"))


if __name__ == "__main__":
    unittest.main()
