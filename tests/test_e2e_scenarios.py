"""End-to-End Evaluation Suite for Dinnerguest.

Simulates complete user lifecycles and validates cross-tool workflows:
1. On-Hand Discovery -> Scaling -> Allergen Verification.
2. Vibe Dialogue -> Conversational Narrowing -> 5-Question Fallback Guardrail.
3. Multi-Course Event Planning -> Auto-Scaling -> Persistent Memory -> Aisle Grocery List.
4. Firestore Cloud Persistence Mock & Fallback.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dinnerguest.agent import (
    check_allergens_for_ingredients,
    find_recipes_by_on_hand_ingredients,
    generate_grocery_list_for_menu,
    get_event_menu,
    list_event_menus,
    root_agent,
    save_event_menu,
    scale_recipe_servings,
    search_recipes_by_vibe,
)
from dinnerguest.agents.vibe_agent import (
    MAX_CLARIFYING_QUESTIONS,
    VibeDialogueState,
    VibeRecommendationAgent,
)
from dinnerguest.tools.menu_store import EventMenuStore


class TestEndToEndScenarios(unittest.TestCase):
    """Integration scenarios testing the end-to-end user journeys."""

    def test_scenario_on_hand_to_scale_to_allergen(self) -> None:
        """Scenario 1: Find recipe from pantry ingredients, scale yield, and verify allergens."""
        # 1. User searches on-hand ingredients (beef, tomatoes, yellow onion, spices)
        search_result = find_recipes_by_on_hand_ingredients(
            ingredients=["ground beef", "tomatoes", "yellow onion", "dry spices"],
            max_missing=0,
        )
        self.assertGreater(search_result["total_matches_count"], 0)
        top_recipe = search_result["recipes"][0]
        self.assertIn("Beef", top_recipe["title"])

        # 2. Scale from 4 servings to 10 guests
        scaled_result = scale_recipe_servings(
            recipe_title=top_recipe["title"],
            ingredients=[
                "1 lb ground beef",
                "2 medium tomatoes, diced",
                "1 yellow onion, chopped",
                "2 tbsp soy sauce",
                "1 tbsp olive oil",
            ],
            original_servings=top_recipe["servings"],
            target_servings=10,
        )
        self.assertEqual(scaled_result["scale_factor"], 2.5)
        self.assertEqual(scaled_result["target_servings"], 10)

        # Verify no LaTeX math syntax in scaled text
        for ing in scaled_result["scaled_ingredients"]:
            self.assertNotIn(r"\frac", ing["scaled_text"])
            self.assertNotIn("$", ing["scaled_text"])

        # 3. Deterministic allergen check on scaled recipe
        scaled_ing_strings = [i["scaled_text"] for i in scaled_result["scaled_ingredients"]]
        allergen_report = check_allergens_for_ingredients(scaled_ing_strings)
        self.assertIn("soybeans", allergen_report["detected_allergens"])  # from soy sauce
        self.assertNotIn("peanuts", allergen_report["detected_allergens"])
        self.assertNotIn("tree_nuts", allergen_report["detected_allergens"])

    def test_scenario_vibe_dialogue_to_guardrail_fallback(self) -> None:
        """Scenario 2: Vibe discovery conversational narrowing and 5-turn hard guardrail."""
        agent = VibeRecommendationAgent()

        # Turn 1: Vague craving
        reply_1 = agent.handle_message("I'm looking for something cozy to cook")
        self.assertEqual(reply_1.state.clarifying_turn_count, 1)
        self.assertTrue(reply_1.is_clarifying_question)
        self.assertIn("savory or sweet", reply_1.reply_text.lower())

        # Simulate user reaching max 5 clarifying turns with vague non-matching input
        state = VibeDialogueState(initial_query="completely vague food", clarifying_turn_count=5)
        reply_5 = agent.handle_message("I'm still not really sure about anything", state=state)

        self.assertFalse(reply_5.is_clarifying_question)
        self.assertEqual(reply_5.state.status, "EXHAUSTED_FALLBACK")
        # Verify polite fallback triggered
        self.assertIn("apologize", reply_5.reply_text.lower())
        self.assertIn("more details", reply_5.reply_text.lower())

    def test_scenario_event_planning_to_grocery_list(self) -> None:
        """Scenario 3: Multi-course event menu planning, persistent recall, and grocery aggregation."""
        event_name = "E2E Graduation Feast"

        # 1. Host plans a 3-course menu for 8 guests
        dishes = [
            {
                "recipe_title": "Spicy Pork Tacos",
                "original_servings": 4,
                "course": "Main",
                "ingredients": [
                    "1 lb pork shoulder",
                    "8 corn tortillas",
                    "1/2 cup salsa",
                    "1 tbsp olive oil",
                ],
            },
            {
                "recipe_title": "Chopped Green Salad",
                "original_servings": 4,
                "course": "Side",
                "ingredients": [
                    "4 cups romaine lettuce",
                    "2 tbsp olive oil",
                    "1 tbsp red wine vinegar",
                ],
            },
            {
                "recipe_title": "Chocolate Chip Cookies",
                "original_servings": 4,
                "course": "Dessert",
                "ingredients": [
                    "1 cup all-purpose flour",
                    "1/2 cup butter",
                    "1/2 cup sugar",
                    "1 egg",
                ],
            },
        ]

        save_res = save_event_menu(
            name=event_name,
            guest_count=8,
            dishes=dishes,
            theme="Celebratory outdoor dinner",
        )
        self.assertEqual(save_res["name"], event_name)
        self.assertEqual(save_res["guest_count"], 8)
        self.assertEqual(save_res["dish_count"], 3)

        # 2. Host recalls the saved menu
        get_res = get_event_menu(event_name)
        self.assertEqual(get_res["name"], event_name)

        # Verify pork shoulder was scaled from 1 lb (for 4) to 2 lb (for 8)
        pork_dish = next(d for d in get_res["dishes"] if d["recipe_title"] == "Spicy Pork Tacos")
        self.assertTrue(any("2 lb pork shoulder" in ing for ing in pork_dish["scaled_ingredients"]))

        # 3. Host generates consolidated grocery list
        g_res = generate_grocery_list_for_menu(event_name)
        self.assertIn("by_aisle", g_res)

        # Olive oil was in Tacos (1 tbsp * 2 = 2 tbsp) and Salad (2 tbsp * 2 = 4 tbsp)
        # Total olive oil = 6 tbsp (or ~0.38 cups)
        oil_item = next((item for item in g_res["items"] if "olive oil" in item["canonical_name"].lower()), None)
        self.assertIsNotNone(oil_item)
        self.assertEqual(len(oil_item["source_recipes"]), 2)

        # Verify aisle classification is present
        self.assertIn("Produce", g_res["by_aisle"])
        self.assertIn("Baking", g_res["by_aisle"])
        self.assertIn("Dairy & Eggs", g_res["by_aisle"])

    def test_scenario_firestore_backend_integration(self) -> None:
        """Scenario 4: Verify Firestore persistence hooks with mocked Firestore client."""
        mock_db = MagicMock()
        mock_col = MagicMock()
        mock_doc = MagicMock()

        mock_db.collection.return_value = mock_col
        mock_col.document.return_value = mock_doc

        store = EventMenuStore(
            storage_path="/tmp/test_menus_firestore_mock.json",
            use_firestore=True,
            firestore_client=mock_db,
        )
        self.assertTrue(store.use_firestore)

        # Save a menu
        saved = store.save_menu(
            name="Mock Firestore Gala",
            guest_count=6,
            dishes=[{"recipe_title": "Tacos", "ingredients": ["1 lb beef"], "original_servings": 4}],
        )
        self.assertEqual(saved.name, "Mock Firestore Gala")
        mock_db.collection.assert_called_with("saved_menus")
        mock_col.document.assert_called()

        # Delete menu
        mock_col.stream.return_value = [
            MagicMock(id="mock-firestore-gala", to_dict=lambda: saved.to_dict())
        ]
        deleted = store.delete_menu("Mock Firestore Gala")
        self.assertTrue(deleted)
        mock_doc.delete.assert_called_once()

    def test_root_orchestrator_capabilities(self) -> None:
        """Scenario 5: Verify root agent exposes all Phase 1-6 tools."""
        tool_names = [getattr(t, "__name__", str(t)) for t in root_agent.tools]
        expected_tools = [
            "find_recipes_by_on_hand_ingredients",
            "search_recipes_by_vibe",
            "check_allergens_for_ingredients",
            "scale_recipe_servings",
            "generate_grocery_list",
            "save_event_menu",
            "get_event_menu",
            "list_event_menus",
            "generate_grocery_list_for_menu",
        ]
        for exp in expected_tools:
            self.assertIn(exp, tool_names)


if __name__ == "__main__":
    unittest.main()
