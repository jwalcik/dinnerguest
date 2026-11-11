"""Tests for MenuPlanningAgent."""

import tempfile
import unittest
from pathlib import Path

from dinnerguest.agents.menu_agent import MenuPlanningAgent
from dinnerguest.tools.menu_store import EventMenuStore


class TestMenuPlanningAgent(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "test_menus.json"
        self.store = EventMenuStore(storage_path=self.storage_path)
        self.agent = MenuPlanningAgent(menu_store=self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_plan_and_save_event_menu(self):
        """Should plan, scale, and save a multi-course menu."""
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
                    "0.5 cup granulated sugar",
                ],
            },
        ]

        summary = self.agent.plan_and_save_event_menu(
            name="Holiday Feast",
            guest_count=8,
            dishes=dishes,
            theme="Festive dinner",
        )

        self.assertEqual(summary.name, "Holiday Feast")
        self.assertEqual(summary.guest_count, 8)
        self.assertEqual(len(summary.dishes), 2)
        self.assertGreater(summary.grocery_item_count, 0)

        # Check serialization
        d = summary.to_dict()
        self.assertEqual(d["name"], "Holiday Feast")
        self.assertIn("consolidated_allergens", d)
        self.assertIn("all_dishes_safe_for", d)

    def test_dietary_safety_and_allergen_free_courses(self):
        """Identifies which allergens are absent across the entire menu."""
        dishes = [
            {
                "recipe_title": "Pork Carnitas Tacos",
                "course": "Main",
                "original_servings": 4,
                "ingredients": [
                    "1.5 lbs pork shoulder, diced",
                    "8 warm corn tortillas",
                    "1 lime, cut into wedges",
                ],
            },
            {
                "recipe_title": "Apple Cinnamon Crisp",
                "course": "Dessert",
                "original_servings": 4,
                "ingredients": [
                    "3 medium apples, sliced",
                    "0.5 cup granulated sugar",
                    "0.33 cup unsalted butter, softened",
                    "1 tsp ground cinnamon",
                ],
            },
        ]

        summary = self.agent.plan_and_save_event_menu(
            name="Taco Night",
            guest_count=4,
            dishes=dishes,
        )

        # Crisp has butter (dairy), Tacos have pork/corn/lime (no major Big 9 allergens)
        self.assertIn("Milk & Dairy", summary.consolidated_allergens)
        self.assertFalse(summary.all_dishes_safe_for["Milk & Dairy"])
        # Both dishes are nut-free, peanut-free, fish-free, shellfish-free
        self.assertTrue(summary.all_dishes_safe_for["Peanuts"])
        self.assertTrue(summary.all_dishes_safe_for["Tree Nuts"])
        self.assertTrue(summary.all_dishes_safe_for["Fish"])

        # Main course has zero detected allergens
        self.assertTrue(any("Main: Pork Carnitas Tacos" in c for c in summary.allergen_free_courses))

    def test_generate_menu_grocery_list(self):
        """Generates a consolidated shopping list for an entire saved event menu."""
        dishes = [
            {
                "recipe_title": "Course 1",
                "ingredients": ["1 cup granulated sugar", "1 lb ground beef"],
            },
            {
                "recipe_title": "Course 2",
                "ingredients": ["0.5 cup granulated sugar", "2 medium tomatoes, diced"],
            },
        ]
        self.agent.plan_and_save_event_menu(
            name="Combined Meal",
            guest_count=4,
            dishes=dishes,
        )

        grocery_list = self.agent.generate_menu_grocery_list("Combined Meal")
        self.assertIsNotNone(grocery_list)
        self.assertEqual(grocery_list.recipe_count, 2)

        # Sugar should be combined (1 + 0.5 = 1.5 cups)
        sugar = next(i for i in grocery_list.items if "sugar" in i.canonical_name.lower())
        self.assertAlmostEqual(sugar.quantity, 1.5, places=1)


if __name__ == "__main__":
    unittest.main()
