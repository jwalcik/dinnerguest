"""Unit tests for Deterministic Allergen Checker."""

import unittest
from pathlib import Path
from dinnerguest.tools.allergen_tool import AllergenChecker


class TestAllergenChecker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = AllergenChecker()

    def test_direct_allergen_detection(self):
        ingredients = [
            "1 cup whole milk",
            "2 large eggs",
            "0.5 cup peanut butter",
            "1 cup all-purpose flour",
            "8 oz raw shrimp",
        ]
        report = self.checker.check_recipe_ingredients(ingredients)

        self.assertTrue(report.has_allergens)
        detected = report.detected_allergens

        self.assertIn("milk", detected)
        self.assertIn("eggs", detected)
        self.assertIn("peanuts", detected)
        self.assertIn("wheat", detected)
        self.assertIn("crustacean_shellfish", detected)

    def test_compound_derivatives_worcestershire(self):
        # Worcestershire sauce contains anchovy (fish)
        ingredients = [
            "1.5 lbs ground beef",
            "1 tbsp worcestershire sauce",
            "1 tsp black pepper",
        ]
        report = self.checker.check_recipe_ingredients(ingredients)

        self.assertIn("fish", report.detected_allergens)
        match = report.detected_allergens["fish"][0]
        self.assertEqual(match.matched_term, "worcestershire sauce")
        self.assertIn("Contains: Fish.", report.get_guest_warning_summary())

    def test_compound_derivatives_pesto(self):
        # Pesto contains pine nuts (tree nuts) and parmesan (milk)
        ingredients = [
            "2 cups cooked penne pasta",
            "0.5 cup traditional pesto",
        ]
        report = self.checker.check_recipe_ingredients(ingredients)

        self.assertIn("tree_nuts", report.detected_allergens)
        self.assertIn("milk", report.detected_allergens)
        self.assertIn("wheat", report.detected_allergens)  # from pasta

    def test_sesame_and_tahini_detection(self):
        ingredients = ["2 tbsp tahini", "1 tsp toasted sesame seeds"]
        report = self.checker.check_recipe_ingredients(ingredients)

        self.assertIn("sesame", report.detected_allergens)
        self.assertTrue(any(m.matched_term == "tahini" for m in report.detected_allergens["sesame"]))

    def test_ambiguous_ingredient_flagging(self):
        ingredients = [
            "1 lb chicken breast",
            "1 tsp natural flavors",
            "1 packet spice blend",
        ]
        report = self.checker.check_recipe_ingredients(ingredients)

        self.assertEqual(len(report.ambiguous_ingredients), 2)
        flagged_terms = [a.flagged_term for a in report.ambiguous_ingredients]
        self.assertIn("natural flavors", flagged_terms)
        self.assertIn("spice blend", flagged_terms)
        self.assertIn("Requires verification", report.get_guest_warning_summary())

    def test_is_safe_for_filtering(self):
        ingredients = ["1 cup almond milk", "1 ripe banana", "1 tbsp honey"]
        report = self.checker.check_recipe_ingredients(ingredients)

        # Contains tree nuts, but peanut and dairy free
        self.assertTrue(report.is_safe_for(["peanuts", "milk", "crustacean_shellfish"]))
        self.assertFalse(report.is_safe_for(["tree_nuts"]))

    def test_multi_dish_menu_allergen_warnings(self):
        menu = {
            "Appetizer: Crostini": ["1 baguette (wheat)", "brie cheese (milk)"],
            "Main: Backyard Burgers": ["ground beef", "worcestershire sauce (fish)", "brioche buns (wheat, eggs)"],
            "Dessert: Fruit Salad": ["strawberries", "blueberries", "mint leaves"],
        }
        menu_report = self.checker.check_menu(menu)

        combined = menu_report["combined_allergens"]
        self.assertIn("Fish", combined)
        self.assertIn("Milk & Dairy", combined)
        self.assertIn("Wheat & Gluten", combined)
        self.assertIn("Eggs", combined)
        self.assertIn("Guest Notice", menu_report["overall_warning"])


if __name__ == "__main__":
    unittest.main()
