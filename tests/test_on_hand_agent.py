"""Unit tests for On-Hand Ingredient Recommendations Sub-Agent."""

import unittest
from dinnerguest.agents.on_hand_agent import OnHandRecommendationAgent


class TestOnHandAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = OnHandRecommendationAgent()

    def test_conversational_request_and_exact_match(self):
        query = "I have a pound of ground beef, two tomatoes, an onion, and some basic dry spices, what can I make?"
        response = self.agent.process_request(query)

        self.assertGreater(response.total_matches, 0)
        self.assertLessEqual(response.presented_count, 5)

        # Recipe 005 (Skillet) should be top choice
        top_recipe = response.recipes[0]
        self.assertEqual(top_recipe.recipe_id, "recipe_005")
        self.assertTrue(top_recipe.is_exact_match)
        self.assertIn("Homestyle Ground Beef & Tomato Skillet", response.reply_text)

    def test_top_five_pagination_dialogue_rule(self):
        query = "ground beef, tomatoes, yellow onion, salt, pepper"
        response = self.agent.process_request(query, max_missing=2, page_size=5)

        if response.total_matches > 5:
            self.assertEqual(response.presented_count, 5)
            self.assertTrue(response.has_more)
            self.assertIn(f"total of **{response.total_matches} recipes**", response.reply_text)
            self.assertIn("Would you like to explore some of the other", response.reply_text)

    def test_substitution_guidance_in_reply(self):
        query = ["ground beef", "yellow onion", "soy sauce", "garlic", "sesame oil", "sugar"]
        response = self.agent.process_request(query, max_missing=0)

        self.assertIn("Substitution", response.reply_text)
        self.assertIn("yellow onion", response.reply_text)
        self.assertIn("shallot", response.reply_text)

    def test_allergen_screening_in_reply(self):
        query = ["1.5 lbs ground beef", "garlic powder", "salt", "pepper", "brioche burger buns", "cheddar cheese"]
        response = self.agent.process_request(query, max_missing=1)

        # Cheeseburger recipe has cheese (milk), buns (wheat/eggs), and worcestershire (fish)
        cheeseburger_matches = [r for r in response.recipes if r.recipe_id == "recipe_006"]
        self.assertTrue(len(cheeseburger_matches) > 0)
        self.assertIn("Allergen Notice", response.reply_text)

    def test_staple_followup_probing(self):
        # Missing staples like pasta or oil
        query = ["ground beef", "tomatoes", "onion", "garlic"]
        response = self.agent.process_request(query, max_missing=2)

        if response.staple_followups:
            self.assertIn("💡 *Pantry Check:*", response.reply_text)
            self.assertTrue(any("on hand?" in p for p in response.staple_followups))

    def test_dessert_query_with_unused_onion(self):
        # User has apples, brown sugar, oats, and an onion, and asks for a dessert
        query = "I have apples, brown sugar, oats, and an onion, is there a dessert I can make?"
        response = self.agent.process_request(query)

        self.assertGreater(response.total_matches, 0)
        top_recipe = response.recipes[0]
        self.assertIn("Crisp", top_recipe.title)
        # Onion was unused
        self.assertTrue(any("onion" in u.lower() for u in top_recipe.unused_ingredients))
        # Reply text notes unused onion
        self.assertIn("Unused Pantry Items", response.reply_text)
        self.assertIn("onion", response.reply_text.lower())
        # Savory recipes are not in the dessert results
        recipe_titles = [r.title.lower() for r in response.recipes]
        self.assertTrue(all("pork" not in t and "skillet" not in t and "bolognese" not in t for t in recipe_titles))

    def test_category_and_ingredient_extraction(self):
        query = "I have apples, brown sugar, oats, and an onion, is there a dessert I can make?"
        category = self.agent.extract_category_from_text(query)
        self.assertEqual(category, "dessert")

        extracted_ings = self.agent.extract_ingredients_from_text(query)
        self.assertIn("apples", [i.lower() for i in extracted_ings])
        self.assertIn("brown sugar", [i.lower() for i in extracted_ings])
        self.assertIn("oats", [i.lower() for i in extracted_ings])
        self.assertTrue(any("onion" in i.lower() for i in extracted_ings))
        # Ensure trailing question was NOT treated as an ingredient
        self.assertFalse(any("dessert" in i.lower() for i in extracted_ings))
        self.assertFalse(any("make" in i.lower() for i in extracted_ings))


if __name__ == "__main__":
    unittest.main()
