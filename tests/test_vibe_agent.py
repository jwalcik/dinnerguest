"""Unit tests for VibeRecommendationAgent dialogue and guardrails."""

import unittest
from unittest.mock import patch

from dinnerguest.agents.vibe_agent import (
    MAX_CLARIFYING_QUESTIONS,
    VibeDialogueState,
    VibeRecommendationAgent,
    vibe_agent,
)
from dinnerguest.tools.vibe_search_tool import VibeSearchTool


class TestVibeAgent(unittest.TestCase):
    def setUp(self):
        self.agent = VibeRecommendationAgent()

    def test_vibe_agent_adk_definition(self):
        """Validates that vibe_agent is defined as an ADK Agent instance."""
        self.assertEqual(vibe_agent.name, "VibeAgent")
        self.assertEqual(vibe_agent.model, "gemini-3.8-flash")
        self.assertIsNotNone(vibe_agent.instruction)
        self.assertGreaterEqual(len(vibe_agent.tools), 1)

    def test_vector_search_prioritized_when_sql_present(self):
        """Validates that search_by_vibe queries Cloud SQL pgvector when present."""
        tool = VibeSearchTool()
        mock_db_results = [
            {
                "id": "recipe_sql_001",
                "title": "Cloud SQL Vector Seared Tuna",
                "servings": 2,
                "prep_time_minutes": 10,
                "cook_time_minutes": 10,
                "tags": ["spicy", "seafood", "quick"],
                "score": 0.95,
                "ingredients": ["fresh tuna steak", "wasabi", "soy sauce"],
                "instructions": ["Sear tuna on high heat for 1 minute per side."],
            }
        ]
        with patch("dinnerguest.database.search_recipes_by_vector", return_value=mock_db_results) as mock_search:
            results = tool.search_by_vibe("spicy tuna")
            mock_search.assert_called_once_with("spicy tuna", limit=5)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Cloud SQL Vector Seared Tuna")

    def test_fallback_to_local_json_only_when_sql_absent(self):
        """Validates that search_by_vibe falls back to local JSON only when DB returns None."""
        tool = VibeSearchTool()
        with patch("dinnerguest.database.search_recipes_by_vector", return_value=None):
            results = tool.search_by_vibe("spicy tuna")
            self.assertGreaterEqual(len(results), 1)
            self.assertIn("Tuna", results[0].title)

    def test_spicy_tuna_never_returns_dan_dan_pork(self):
        """Validates that a query for spicy tuna returns tuna and not Dan Dan pork noodles."""
        tool = VibeSearchTool()
        with patch("dinnerguest.database.search_recipes_by_vector", return_value=None):
            results = tool.search_by_vibe("I'd like to cook a spicy tuna dish")
            self.assertGreaterEqual(len(results), 1)
            self.assertIn("Tuna", results[0].title)
            self.assertNotIn("Dan Dan Pork", results[0].title)

    def test_spicy_pork_clarifying_question(self):
        # User asks: "I would like a spicy pork dish"
        # Agent should ask: "Would you prefer a spicy taco recipe or a noodle dish?"
        response = self.agent.handle_message("I would like a spicy pork dish")
        self.assertTrue(response.is_clarifying_question)
        self.assertEqual(response.state.clarifying_turn_count, 1)
        self.assertIn("taco", response.reply_text.lower())
        self.assertIn("noodle", response.reply_text.lower())

        # Turn 2: User responds "noodle dish"
        response2 = self.agent.handle_message("I'd love a noodle dish", state=response.state)
        self.assertFalse(response2.is_clarifying_question)
        self.assertEqual(response2.state.status, "RECOMMENDED")
        self.assertTrue(len(response2.recommended_recipes) > 0)
        top = response2.recommended_recipes[0]
        self.assertIn("Noodles", top.title)
        self.assertIn("Allergen Notice", response2.reply_text)

    def test_cozy_clarifying_question(self):
        # User asks: "What's something cozy I can cook?"
        # Agent should ask: "Would you like something savory or sweet?"
        response = self.agent.handle_message("What's something cozy I can cook?")
        self.assertTrue(response.is_clarifying_question)
        self.assertEqual(response.state.clarifying_turn_count, 1)
        self.assertIn("savory", response.reply_text.lower())
        self.assertIn("sweet", response.reply_text.lower())

        # Turn 2: User responds "sweet"
        response2 = self.agent.handle_message("I'm in the mood for something sweet", state=response.state)
        # Asks cookies vs fruit crisp
        self.assertTrue(response2.is_clarifying_question)
        self.assertEqual(response2.state.clarifying_turn_count, 2)

        # Turn 3: User responds "fruit crisp"
        response3 = self.agent.handle_message("A fruit crisp sounds great", state=response2.state)
        self.assertFalse(response3.is_clarifying_question)
        self.assertEqual(response3.state.status, "RECOMMENDED")
        self.assertIn("Apple Cinnamon", response3.reply_text)

    def test_max_five_clarifying_questions_guardrail_and_fallback(self):
        # User gives completely vague responses that cannot be pinpointed
        state = VibeDialogueState(initial_query="I want food")

        # Simulate 5 consecutive clarifying turns
        for turn in range(MAX_CLARIFYING_QUESTIONS):
            resp = self.agent.handle_message(f"Vague response {turn + 1}", state=state)
            if turn < MAX_CLARIFYING_QUESTIONS - 1:
                self.assertTrue(resp.is_clarifying_question)
                self.assertEqual(resp.state.clarifying_turn_count, turn + 1)

        # On the 5th/6th attempt exceeding the limit:
        resp_final = self.agent.handle_message("Still completely vague", state=state)
        # Guardrail stops asking clarifying questions!
        self.assertFalse(resp_final.is_clarifying_question)
        self.assertIn(resp_final.state.status, ["RECOMMENDED", "EXHAUSTED_FALLBACK"])

        if resp_final.state.status == "EXHAUSTED_FALLBACK":
            self.assertIn("apologize", resp_final.reply_text.lower())
            self.assertIn("more details", resp_final.reply_text.lower())

    def test_clean_recipe_text_list_formats(self):
        from dinnerguest.database import clean_recipe_text_list
        # Python repr string list
        self.assertEqual(
            clean_recipe_text_list("['cabbage', 'soy sauce', 'garlic']"),
            ["cabbage", "soy sauce", "garlic"]
        )
        # Nested stringified list
        self.assertEqual(
            clean_recipe_text_list(["['cabbage', 'soy sauce']"]),
            ["cabbage", "soy sauce"]
        )
        # JSON string list
        self.assertEqual(
            clean_recipe_text_list('["salt", "pepper"]'),
            ["salt", "pepper"]
        )

    def test_build_recommendation_reply_formatting(self):
        from dinnerguest.tools.vibe_search_tool import VibeRecipeResult
        recipe = VibeRecipeResult(
            recipe_id="test_001",
            title="Spicy Noodles",
            servings=2,
            prep_time_minutes=10,
            cook_time_minutes=15,
            tags=["spicy", "asian"],
            matched_vibes=["spicy"],
            score=0.9,
            ingredients="['cabbage', 'soy sauce', 'chili oil']",  # Raw Kaggle string
            instructions="['boil water', 'cook noodles 5 min', 'toss with sauce']",
        )
        # VibeRecipeResult should auto-unpack and clean lists in __post_init__
        self.assertEqual(recipe.ingredients, ["cabbage", "soy sauce", "chili oil"])
        self.assertEqual(recipe.instructions, ["boil water", "cook noodles 5 min", "toss with sauce"])

        state = VibeDialogueState(initial_query="spicy noodles")
        response = self.agent._build_recommendation_reply(recipe, state)

        self.assertIn("🍲 Here is a great recipe for **Spicy Noodles**:", response.reply_text)
        self.assertIn("- **Servings**: 2", response.reply_text)
        self.assertIn("- **Time**: 10 min prep, 15 min cook", response.reply_text)
        self.assertIn("### Ingredients:", response.reply_text)
        self.assertIn("• Cabbage", response.reply_text)
        self.assertIn("• Soy sauce", response.reply_text)
        self.assertIn("### Instructions:", response.reply_text)
        self.assertIn("1. Boil water.", response.reply_text)
        self.assertIn("2. Cook noodles 5 min.", response.reply_text)
        self.assertIn("3. Toss with sauce.", response.reply_text)


if __name__ == "__main__":
    unittest.main()
