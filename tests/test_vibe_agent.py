"""Unit tests for VibeRecommendationAgent dialogue and guardrails."""

import unittest
from dinnerguest.agents.vibe_agent import (
    MAX_CLARIFYING_QUESTIONS,
    VibeDialogueState,
    VibeRecommendationAgent,
)


class TestVibeAgent(unittest.TestCase):
    def setUp(self):
        self.agent = VibeRecommendationAgent()

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


if __name__ == "__main__":
    unittest.main()
