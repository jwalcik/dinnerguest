"""Unit tests for Pantry & On-Hand Search Tool."""

import unittest
from dinnerguest.tools.pantry_search_tool import PantrySearchTool, normalize_ingredient_phrase


class TestPantrySearchTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.search_tool = PantrySearchTool()

    def test_normalize_ingredient_phrase(self):
        self.assertEqual(normalize_ingredient_phrase("1 lb 80/20 ground beef"), "ground beef")
        self.assertEqual(normalize_ingredient_phrase("1 medium yellow onion, diced"), "yellow onion")
        self.assertEqual(normalize_ingredient_phrase("2 cloves garlic, minced"), "garlic")
        self.assertEqual(normalize_ingredient_phrase("0.5 cup breadcrumbs"), "breadcrumbs")
        self.assertEqual(normalize_ingredient_phrase("2 medium tomatoes, diced"), "tomatoes")

    def test_exact_match_skillet(self):
        # User has beef, tomatoes, onion, dry spices
        user_items = ["ground beef", "tomatoes", "yellow onion", "dry spices"]
        result = self.search_tool.search_recipes(user_items, max_missing=0)

        self.assertGreaterEqual(result.total_matches_count, 1)
        top_recipe = result.recipes[0]
        self.assertEqual(top_recipe.recipe_id, "recipe_005")  # Homestyle Skillet
        self.assertTrue(top_recipe.is_exact_match)
        self.assertEqual(len(top_recipe.missing_ingredients), 0)

    def test_substitution_detection_shallots_to_onion(self):
        # User has beef, yellow onion, soy sauce, garlic, sesame oil, sugar
        # Recipe_009 requires shallots!
        user_items = ["ground beef", "yellow onion", "soy sauce", "garlic", "sesame oil", "sugar"]
        result = self.search_tool.search_recipes(user_items, max_missing=0)

        match_ids = [r.recipe_id for r in result.recipes]
        self.assertIn("recipe_009", match_ids)

        stir_fry = next(r for r in result.recipes if r.recipe_id == "recipe_009")
        self.assertEqual(len(stir_fry.substituted_ingredients), 1)
        sub = stir_fry.substituted_ingredients[0]
        self.assertIn("shallot", sub.recipe_ingredient)
        self.assertEqual(sub.user_ingredient, "yellow onion")

    def test_pagination_and_top_five_limit(self):
        # With ground beef, tomatoes, onion, spices, garlic, olive oil, 7 recipes match with max_missing=3
        user_items = ["ground beef", "tomatoes", "onion", "dry spices", "garlic", "olive oil"]
        result = self.search_tool.search_recipes(user_items, max_missing=3, page=1, page_size=5)

        self.assertGreater(result.total_matches_count, 5)
        self.assertEqual(len(result.recipes), 5)
        self.assertTrue(result.has_more)
        self.assertEqual(result.remaining_count, result.total_matches_count - 5)

        # Verify page 2 retrieves the remainder
        page2 = self.search_tool.search_recipes(user_items, max_missing=3, page=2, page_size=5)
        self.assertEqual(len(page2.recipes), result.remaining_count)
        self.assertFalse(page2.has_more)

    def test_staple_followup_generation(self):
        # If user has ground beef, tomatoes, onion, garlic - recipe_008 (Bolognese) is missing pasta and olive oil
        user_items = ["ground beef", "tomatoes", "onion", "garlic", "salt", "black pepper"]
        result = self.search_tool.search_recipes(user_items, max_missing=2)

        self.assertTrue(len(result.staple_followup_prompts) > 0)
        # Prompts should suggest common staples
        prompts_str = " ".join(result.staple_followup_prompts).lower()
        self.assertTrue("pasta" in prompts_str or "olive oil" in prompts_str or "oregano" in prompts_str)


if __name__ == "__main__":
    unittest.main()
