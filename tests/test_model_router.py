"""Unit tests for Strategic Model Routing across MenuAgent and OnHandAgent."""

import unittest
from unittest.mock import MagicMock

from dinnerguest.agents.menu_agent import menu_agent
from dinnerguest.agents.on_hand_agent import OnHandRecommendationAgent, on_hand_agent
from dinnerguest.model_router import CulinaryModelRouter


class TestModelRouter(unittest.TestCase):
    def setUp(self):
        self.router = CulinaryModelRouter(
            fast_model="gemini-3.8-flash",
            reasoning_model="gemini-4.0-carbon",
        )

    def test_menu_agent_uses_gemini_4_0_carbon(self):
        """MenuAgent should be configured to use gemini-4.0-carbon."""
        self.assertEqual(menu_agent.model, "gemini-4.0-carbon")
        decision = self.router.route_menu_planning_turn()
        self.assertEqual(decision.model, "gemini-4.0-carbon")
        self.assertEqual(decision.tier, "reasoning")
        self.assertEqual(decision.target_agent, "MenuAgent")

    def test_on_hand_direct_matches_use_gemini_3_8_flash(self):
        """Direct on-hand matches without substitutions should route to gemini-3.8-flash."""
        direct_result = {
            "recipes": [
                {
                    "recipe_id": "r1",
                    "title": "Homestyle Ground Beef & Tomato Skillet",
                    "is_exact_match": True,
                    "matched_ingredients": ["ground beef", "tomatoes", "onion"],
                    "substituted_ingredients": [],
                    "missing_ingredients": [],
                }
            ]
        }
        decision = self.router.route_on_hand_turn(
            user_msg="I have ground beef, tomatoes, and onion",
            tool_result=direct_result,
        )
        self.assertEqual(decision.model, "gemini-3.8-flash")
        self.assertEqual(decision.tier, "fast")
        self.assertFalse(decision.requires_substitution_reasoning)

    def test_on_hand_substitutions_route_to_gemini_4_0_carbon(self):
        """On-hand matches requiring ingredient substitutions should route to gemini-4.0-carbon."""
        sub_result = {
            "recipes": [
                {
                    "recipe_id": "recipe_012",
                    "title": "Cozy Warm Apple Cinnamon Crisp",
                    "is_exact_match": True,
                    "matched_ingredients": ["apples", "oats"],
                    "substituted_ingredients": [
                        {
                            "recipe_ingredient": "0.5 cup granulated sugar",
                            "used_on_hand": "brown sugar",
                            "explanation": "Substituted brown sugar for granulated sugar",
                        }
                    ],
                    "missing_ingredients": [],
                }
            ]
        }
        decision = self.router.route_on_hand_turn(
            user_msg="I have apples, brown sugar, oats, and an onion, is there a dessert I can make?",
            tool_result=sub_result,
        )
        self.assertEqual(decision.model, "gemini-4.0-carbon")
        self.assertEqual(decision.tier, "reasoning")
        self.assertTrue(decision.requires_substitution_reasoning)

    def test_on_hand_agent_process_request_records_routed_model(self):
        """OnHandRecommendationAgent should record gemini-4.0-carbon when substitutions are used."""
        agent = OnHandRecommendationAgent(model_router=self.router)
        # Apples + brown sugar + oats uses brown sugar substitution for granulated sugar in Apple Crisp
        resp_sub = agent.process_request(
            "I have apples, brown sugar, oats, and an onion, is there a dessert I can make?",
            category="dessert",
        )
        self.assertEqual(resp_sub.selected_model, "gemini-4.0-carbon")
        self.assertIsNotNone(resp_sub.routing_decision)
        self.assertTrue(resp_sub.routing_decision.requires_substitution_reasoning)

    def test_adk_on_hand_model_routing_callback(self):
        """ADK before_model_callback on OnHandAgent should switch llm_request.model dynamically."""
        self.assertIsNotNone(on_hand_agent.before_model_callback)
        mock_ctx = MagicMock()
        mock_ctx.state = {
            "last_on_hand_result": {
                "recipes": [
                    {
                        "recipe_id": "r1",
                        "title": "Sub Recipe",
                        "is_exact_match": False,
                        "substituted_ingredients": [{"used_on_hand": "shallot"}],
                        "missing_ingredients": ["stock"],
                    }
                ]
            }
        }
        mock_req = MagicMock()
        mock_req.model = "gemini-3.8-flash"

        self.router.adk_on_hand_model_routing_callback(mock_ctx, mock_req)
        self.assertEqual(mock_req.model, "gemini-4.0-carbon")
        self.assertEqual(mock_ctx.state["active_model"], "gemini-4.0-carbon")


if __name__ == "__main__":
    unittest.main()
