"""Unit tests for Dinnerguest ADK Agent definition and tool bindings."""

import unittest
from dinnerguest.agent import (
    check_allergens_for_ingredients,
    find_recipes_by_on_hand_ingredients,
    generate_grocery_list,
    root_agent,
    scale_recipe_servings,
    search_recipes_by_vibe,
)


class TestDinnerguestAgent(unittest.TestCase):
    def test_root_agent_metadata(self):
        self.assertEqual(root_agent.name, "Dinnerguest")
        self.assertIsNotNone(root_agent.instruction)
        self.assertEqual(len(root_agent.tools), 5)

    def test_generate_grocery_list_tool(self):
        recipes = [
            {"title": "Cookies", "ingredients": ["1 cup sugar"]},
            {"title": "Crisp", "ingredients": ["0.5 cup sugar"]},
        ]
        result = generate_grocery_list(recipes)
        self.assertIn("items", result)
        self.assertIn("by_aisle", result)
        self.assertEqual(result["recipe_count"], 2)

    def test_search_recipes_by_vibe_tool(self):
        result = search_recipes_by_vibe("spicy pork", format="noodle")
        self.assertGreaterEqual(len(result), 1)
        self.assertIn("Noodles", result[0]["title"])
        self.assertIn("allergen_summary", result[0])

    def test_find_recipes_tool(self):
        result = find_recipes_by_on_hand_ingredients(
            ingredients=["ground beef", "tomatoes", "onion", "dry spices"],
            max_missing=1,
            page=1,
        )
        self.assertIn("recipes", result)
        self.assertIn("total_matches_count", result)
        self.assertGreaterEqual(result["total_matches_count"], 1)

    def test_check_allergens_tool(self):
        result = check_allergens_for_ingredients(["1 lb shrimp", "2 tbsp butter"])
        self.assertTrue(result["has_allergens"])
        self.assertIn("Milk & Dairy", result["unique_allergens"])
        self.assertIn("Crustacean Shellfish", result["unique_allergens"])

    def test_scale_recipe_tool(self):
        result = scale_recipe_servings(
            recipe_title="Test Recipe",
            ingredients=["2 cups flour", "1 tsp salt"],
            original_servings=4,
            target_servings=12,
        )
        self.assertEqual(result["scale_factor"], 3.0)
        self.assertEqual(len(result["scaled_ingredients"]), 2)


if __name__ == "__main__":
    unittest.main()
