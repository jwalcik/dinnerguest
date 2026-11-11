"""Unit tests for Deterministic Recipe Scaler."""

import unittest
from dinnerguest.tools.scaler_tool import RecipeScaler


class TestRecipeScaler(unittest.TestCase):
    def setUp(self):
        self.scaler = RecipeScaler()

    def test_scale_up_proportional(self):
        ingredients = [
            "1 lb ground beef",
            "0.5 cup breadcrumbs",
            "1 large egg",
            "2 cloves garlic",
            "1 tsp salt",
        ]
        result = self.scaler.scale_recipe(
            recipe_title="Classic Meatballs",
            ingredients=ingredients,
            original_servings=4,
            target_servings=12,
        )

        self.assertEqual(result.scale_factor, 3.0)
        self.assertFalse(result.has_warnings)

        scaled_texts = [i.scaled_text for i in result.scaled_ingredients]
        self.assertIn("3 lb ground beef", scaled_texts)
        self.assertIn("1 1/2 cup breadcrumbs", scaled_texts)
        self.assertIn("3 large egg", scaled_texts)
        self.assertIn("6 cloves garlic", scaled_texts)
        self.assertIn("3 tsp salt", scaled_texts)

    def test_scale_down_min_volume_warning_and_recommendation(self):
        # 12 cookies scaled to 1 cookie
        cookie_ingredients = [
            "2.25 cups all-purpose flour",
            "0.75 tsp baking soda",
            "0.5 tsp salt",
            "1 cup unsalted butter",
            "2 cups semi-sweet chocolate chips",
        ]
        result = self.scaler.scale_recipe(
            recipe_title="Chocolate Chip Cookies",
            ingredients=cookie_ingredients,
            original_servings=12,
            target_servings=1,
        )

        self.assertTrue(result.has_warnings)
        min_warnings = [w for w in result.warnings if w.warning_type == "MIN_VOLUME_DIFFICULTY"]
        self.assertTrue(len(min_warnings) >= 1)

        # 0.5 tsp salt * (1/12) = 0.0416 tsp (< 0.125 tsp).
        # Safe min servings = ceil(12 * (0.125 / 0.5)) = 3 or 4
        self.assertIsNotNone(result.recommended_minimum_servings)
        self.assertGreaterEqual(result.recommended_minimum_servings, 3)

        # Check message clarity
        warning_msg = min_warnings[0].message
        self.assertIn("less than 1/8 tsp", warning_msg)
        self.assertIn("Recommended minimum scale", warning_msg)

    def test_scale_up_bulk_weight_warning(self):
        burger_ingredients = [
            "1.5 lbs 80/20 ground beef",
            "1 tbsp worcestershire sauce",
            "4 brioche burger buns",
        ]
        # Scaling 4 burgers to 60 burgers (factor = 15x). Ground beef becomes 22.5 lbs (> 15 lbs threshold)
        result = self.scaler.scale_recipe(
            recipe_title="Backyard Cheeseburgers",
            ingredients=burger_ingredients,
            original_servings=4,
            target_servings=60,
        )

        self.assertTrue(result.has_warnings)
        bulk_warnings = [w for w in result.warnings if w.warning_type == "MAX_BULK_BATCH_DIFFICULTY"]
        self.assertTrue(len(bulk_warnings) >= 1)
        self.assertIn("exceeds standard residential cookware capacity", bulk_warnings[0].message)

    def test_scale_up_bulk_volume_warning(self):
        soup_ingredients = [
            "8 cups chicken broth",
            "1 lb carrots",
        ]
        # Scaling 4 to 32 servings (factor = 8x). Broth becomes 64 cups = 4 gallons (> 1 gallon)
        result = self.scaler.scale_recipe(
            recipe_title="Chicken Soup",
            ingredients=soup_ingredients,
            original_servings=4,
            target_servings=32,
        )

        self.assertTrue(result.has_warnings)
        vol_warnings = [w for w in result.warnings if "gallons" in w.message]
        self.assertTrue(len(vol_warnings) >= 1)

    def test_unquantified_ingredients_preserved(self):
        ingredients = [
            "1 lb pasta",
            "Salt and freshly ground black pepper to taste",
        ]
        result = self.scaler.scale_recipe(
            recipe_title="Simple Pasta",
            ingredients=ingredients,
            original_servings=2,
            target_servings=6,
        )

        scaled_texts = [i.scaled_text for i in result.scaled_ingredients]
        self.assertIn("Salt and freshly ground black pepper to taste", scaled_texts)
        self.assertIn("3 lb pasta", scaled_texts)

    def test_mixed_fraction_scaling(self):
        ingredients = [
            "1 1/2 cups all-purpose flour",
            "2 1/4 tsp active dry yeast",
        ]
        result = self.scaler.scale_recipe(
            recipe_title="Pizza Dough",
            ingredients=ingredients,
            original_servings=2,
            target_servings=4,
        )

        scaled_texts = [i.scaled_text for i in result.scaled_ingredients]
        self.assertIn("3 cups all-purpose flour", scaled_texts)
        self.assertIn("4 1/2 tsp active dry yeast", scaled_texts)


if __name__ == "__main__":
    unittest.main()
