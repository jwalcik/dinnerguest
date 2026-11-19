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

        # Verify page 2 retrieves the next page
        page2 = self.search_tool.search_recipes(user_items, max_missing=3, page=2, page_size=5)
        self.assertEqual(len(page2.recipes), min(5, result.remaining_count))
        self.assertEqual(page2.remaining_count, result.remaining_count - len(page2.recipes))

    def test_staple_followup_generation(self):
        # If user has ground beef, tomatoes, onion, garlic - recipe_008 (Bolognese) is missing pasta and olive oil
        user_items = ["ground beef", "tomatoes", "onion", "garlic", "salt", "black pepper"]
        result = self.search_tool.search_recipes(user_items, max_missing=2)

        self.assertTrue(len(result.staple_followup_prompts) > 0)
        # Prompts should suggest common staples
        prompts_str = " ".join(result.staple_followup_prompts).lower()
        self.assertTrue("pasta" in prompts_str or "olive oil" in prompts_str or "oregano" in prompts_str)

    def test_apple_dessert_with_unused_onion_and_sugar_sub(self):
        # User has apples, brown sugar, oats, and an onion
        # Should match apple crisp dessert and exclude onion
        user_items = ["apples", "brown sugar", "oats", "onion"]
        result = self.search_tool.search_recipes(user_items, category="dessert", max_missing=3)

        self.assertGreater(result.total_matches_count, 0)
        crisp = result.recipes[0]
        self.assertIn("Crisp", crisp.title)
        self.assertIn("onion", crisp.unused_ingredients)
        # Verify brown sugar substitution for granulated sugar
        sub_targets = [s.recipe_ingredient for s in crisp.substituted_ingredients]
        self.assertTrue(any("sugar" in s for s in sub_targets))

    def test_derivative_false_positive_rejection(self):
        from dinnerguest.tools.pantry_search_tool import ingredients_match

        # Fresh ingredients must NEVER match soups, broths, sauces, pastes, or unrelated vegetables
        self.assertFalse(ingredients_match("tomato", "1 (10 oz.) can condensed tomato soup"))
        self.assertFalse(ingredients_match("tomato", "1 cup tomato sauce"))
        self.assertFalse(ingredients_match("chicken breasts", "1 cup chicken broth"))
        self.assertFalse(ingredients_match("chicken breasts", "1 can cream of chicken soup"))
        self.assertFalse(ingredients_match("black pepper", "4 bell peppers, halved"))
        self.assertFalse(ingredients_match("butter", "2 tbsp peanut butter"))

        # Valid singular/plural, comma-separated prep adjectives, and bun/roll equivalences MUST match
        self.assertTrue(ingredients_match("chicken breasts", "4 boneless, skinless chicken breasts"))
        self.assertTrue(ingredients_match("chicken breasts", "4 boneless skinless chicken breast halves"))
        self.assertTrue(ingredients_match("hamburger buns", "4 sandwich buns"))
        self.assertTrue(ingredients_match("hamburger buns", "4 brioche burger buns"))
        self.assertTrue(ingredients_match("ranch dressing", "1/2 cup ranch salad dressing"))

    def test_chicken_bacon_ranch_sandwich_vs_frontier_dinner(self):
        from unittest.mock import patch

        mock_candidates = [
            {
                "id": "kaggle_267371",
                "title": "Frontier Dinner",
                "servings": 4,
                "ingredients": [
                    "1 (7 oz.) pkg. elbow macaroni (2 c.), uncooked",
                    "1 (15 oz.) can chili",
                    "1 (10 oz.) can condensed tomato soup",
                ],
                "instructions": ["Cook macaroni, stir in chili and tomato soup."],
                "tags": [],
            },
            {
                "id": "kaggle_chicken_1",
                "title": "Crispy Chicken Bacon Ranch Sandwiches",
                "servings": 4,
                "ingredients": [
                    "4 boneless, skinless chicken breasts",
                    "4 hamburger buns, toasted",
                    "8 strips bacon, cooked crisp",
                    "4 leaves romaine lettuce",
                    "1 large tomato, sliced",
                    "0.5 cup ranch dressing",
                    "0.5 tsp salt",
                    "0.5 tsp black pepper",
                ],
                "instructions": ["Grill chicken, toast buns, assemble with bacon, lettuce, tomato, and ranch."],
                "tags": [],
            },
        ]
        user_items = ["chicken breasts", "hamburger buns", "lettuce", "tomato", "bacon", "ranch dressing"]
        with patch("dinnerguest.database.fetch_pantry_candidates_from_db", return_value=mock_candidates):
            result = self.search_tool.search_recipes(user_items, category="dinner", max_missing=2)

        titles = [r.title for r in result.recipes]
        self.assertIn("Crispy Chicken Bacon Ranch Sandwiches", titles)
        self.assertNotIn("Frontier Dinner", titles)
        self.assertEqual(result.recipes[0].title, "Crispy Chicken Bacon Ranch Sandwiches")
        self.assertEqual(len(result.recipes[0].unused_ingredients), 0)


if __name__ == "__main__":
    unittest.main()
