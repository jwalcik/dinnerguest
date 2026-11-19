"""Tests for server turn execution logic, trace extraction, and response formatting."""

import unittest
from unittest.mock import MagicMock

from dinnerguest.server import _execute_agent_turn, _extract_trace_id


class TestServerLogic(unittest.TestCase):
    """Test suite covering server execution logic and event dispatching."""

    def test_extract_trace_id_fallback(self):
        tid = _extract_trace_id(None)
        self.assertIsInstance(tid, str)
        self.assertTrue(len(tid) > 0)

    def test_extract_trace_id_from_header(self):
        req = MagicMock()
        req.headers.get.return_value = "105d652c986d7d8758ebaedceca5dd30/1234;o=1"
        tid = _extract_trace_id(req)
        self.assertEqual(tid, "105d652c986d7d8758ebaedceca5dd30")

    def test_on_hand_search_turn(self):
        result = _execute_agent_turn(
            user_msg="I have ground beef, onions, and canned tomatoes",
            session_id="test_sess_1",
            trace_id="test_trace_1",
        )
        self.assertIn("reply", result)
        self.assertEqual(result["session_id"], "test_sess_1")
        self.assertEqual(result["trace_id"], "test_trace_1")
        self.assertTrue(len(result["events"]) > 0)
        # Should include thought and tool call
        event_types = [e["type"] for e in result["events"]]
        self.assertIn("thought", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)

    def test_allergen_screening_turn(self):
        result = _execute_agent_turn(
            user_msg="Check allergens for: milk, peanuts, and flour",
            session_id="test_sess_2",
            trace_id="test_trace_2",
        )
        self.assertIn("Allergen Safety Analysis", result["reply"])
        self.assertIn("milk", result["reply"])
        self.assertIn("peanuts", result["reply"])
        self.assertIn("wheat", result["reply"])
        event_types = [e["type"] for e in result["events"]]
        self.assertIn("thought", event_types)
        self.assertIn("tool_call", event_types)

    def test_recipe_scaling_turn(self):
        result = _execute_agent_turn(
            user_msg="Scale recipe to 12 servings",
            session_id="test_sess_3",
            trace_id="test_trace_3",
        )
        self.assertIn("Scaled recipe yield", result["reply"])
        self.assertIn("scale factor: 3", result["reply"])

    def test_vibe_turn_spicy(self):
        result = _execute_agent_turn(
            user_msg="I am craving a spicy pork dish",
            session_id="test_sess_4",
            trace_id="test_trace_4",
        )
        self.assertIn("reply", result)
        self.assertTrue(len(result["events"]) > 0)

    def test_on_hand_dessert_apples_carrots(self):
        result = _execute_agent_turn(
            user_msg="I would like to make a dessert. I have two apples, half a red onion, butter, sugar, and shredded carrots",
            session_id="test_sess_dessert",
            trace_id="test_trace_dessert",
        )
        self.assertIn("reply", result)
        self.assertTrue("Crisp" in result["reply"])
        self.assertIn("Allergen Notice", result["reply"])

    def test_on_hand_dessert_apples_brown_sugar_oats_onion(self):
        result = _execute_agent_turn(
            user_msg="I have apples, brown sugar, oats, and an onion, is there a dessert I can make?",
            session_id="test_sess_apple_onion_dessert",
            trace_id="test_trace_apple_onion_dessert",
        )
        self.assertIn("reply", result)
        self.assertIn("Apple Cinnamon Crisp", result["reply"])
        self.assertIn("Unused Pantry Items", result["reply"])
        self.assertIn("onion", result["reply"].lower())
        self.assertIn("Allergen Notice", result["reply"])

    def test_recipe_query_chicken_soup(self):
        result = _execute_agent_turn(
            user_msg="I need a recipe for chicken soup.",
            session_id="test_sess_soup",
            trace_id="test_trace_soup",
        )
        self.assertIn("reply", result)
        self.assertIn("Chicken Noodle Soup", result["reply"])
        self.assertIn("Ingredients:", result["reply"])
        self.assertIn("Instructions:", result["reply"])
        self.assertIn("Allergen Notice", result["reply"])

    def test_vibe_cozy_soup_sick(self):
        result = _execute_agent_turn(
            user_msg="I'd like to cook a cozy soup for when I'm feeling sick",
            session_id="test_sess_sick",
            trace_id="test_trace_sick",
        )
        self.assertIn("reply", result)
        self.assertIn("Chicken Noodle Soup", result["reply"])
        self.assertIn("Allergen Notice", result["reply"])

    def test_thanksgiving_dinner_planning(self):
        result = _execute_agent_turn(
            user_msg="I need to plan Thanksgiving dinner",
            session_id="test_sess_tg",
            trace_id="test_trace_tg",
        )
        self.assertIn("reply", result)
        self.assertIn("Thanksgiving Dinner", result["reply"])
        self.assertIn("Turkey Breast", result["reply"])
        self.assertIn("Mashed Potatoes", result["reply"])
        self.assertIn("Stuffing", result["reply"])
        self.assertIn("Apple Cinnamon Crisp", result["reply"])
        self.assertIn("Allergen Profile", result["reply"])

    def test_spicy_potato_dish(self):
        result = _execute_agent_turn(
            user_msg="I'd like to make a spicy potato dish",
            session_id="test_sess_potato",
            trace_id="test_trace_potato",
        )
        self.assertIn("reply", result)
        self.assertIn("Crispy Spicy Garlic Roasted Potatoes", result["reply"])
        self.assertNotIn("Dan Dan Pork", result["reply"])

    def test_spicy_carrot_dish(self):
        result = _execute_agent_turn(
            user_msg="I'd like to make a spicy carrot dish",
            session_id="test_sess_carrot",
            trace_id="test_trace_carrot",
        )
        self.assertIn("reply", result)
        self.assertIn("Spicy Honey-Glazed Roasted Carrots", result["reply"])
        self.assertNotIn("Dan Dan Pork", result["reply"])

    def test_spicy_tuna_dish(self):
        result = _execute_agent_turn(
            user_msg="I'd like to cook a spicy tuna dish",
            session_id="test_sess_tuna",
            trace_id="test_trace_tuna",
        )
        self.assertIn("reply", result)
        self.assertIn("Spicy Seared Ahi Tuna Steaks", result["reply"])
        self.assertNotIn("Dan Dan Pork", result["reply"])

    def test_easter_lunch_planning(self):
        result = _execute_agent_turn(
            user_msg="i would like to plan easter lunch",
            session_id="test_sess_easter",
            trace_id="test_trace_easter",
        )
        self.assertIn("reply", result)
        self.assertIn("Easter Lunch Feast Planned", result["reply"])
        self.assertIn("Tomato Basil Soup", result["reply"])
        self.assertIn("Turkey Breast", result["reply"])
        self.assertIn("Roasted Carrots", result["reply"])
        self.assertIn("Mashed Potatoes", result["reply"])
        self.assertIn("Apple Cinnamon Crisp", result["reply"])
        # Verify tool call was plan_and_save_event_menu
        tool_calls = [e for e in result["events"] if e.get("type") == "tool_call"]
        self.assertTrue(any(tc.get("tool") == "plan_and_save_event_menu" for tc in tool_calls))

    def test_database_status(self):
        from dinnerguest.database import get_database_status
        status = get_database_status()
        self.assertIn("configured", status)
        self.assertIn("connected", status)
        self.assertIn("driver", status)
        self.assertIn("recipe_count", status)

    def test_on_hand_chicken_sandwich_dinner(self):
        result = _execute_agent_turn(
            user_msg="I've got some chicken breasts, hamburger buns, lettuce, tomato, bacon, and ranch dressing. What can I make for dinner?",
            session_id="test_sess_chk_sand",
            trace_id="test_trace_chk_sand",
        )
        self.assertIn("reply", result)
        self.assertIn("Chicken Bacon", result["reply"])
        self.assertIn("Sandwiches", result["reply"])
        self.assertNotIn("Frontier Dinner", result["reply"])

    def test_multi_turn_event_menu_modifications(self):
        sess = "test_sess_menu_mods"
        # Turn 1: Plan Thanksgiving Dinner
        r1 = _execute_agent_turn("I'd like to plan Thanksgiving Dinner", sess, "tr_1")
        self.assertIn("Thanksgiving Dinner", r1["reply"])
        self.assertGreater(r1["data"]["grocery_item_count"], 0)
        self.assertGreater(len(r1["data"]["dishes"][0]["original_ingredients"]), 0)

        # Turn 2: Replace the Apple Crisp with a pecan pie
        r2 = _execute_agent_turn("Replace the Apple Crisp with a pecan pie", sess, "tr_2")
        self.assertIn("Updated 'Thanksgiving Dinner' Menu", r2["reply"])
        self.assertIn("Pecan Pie", r2["reply"])
        self.assertIn("Tree Nuts", r2["reply"])
        dish_titles_t2 = [d["recipe_title"] for d in r2["data"]["dishes"]]
        self.assertTrue(any("Pecan Pie" in t for t in dish_titles_t2))
        self.assertFalse(any("Apple Cinnamon Crisp" in t for t in dish_titles_t2))

        # Turn 3: Add a deviled egg appetizer
        r3 = _execute_agent_turn("Add a deviled egg appetizer", sess, "tr_3")
        self.assertIn("Deviled Eggs", r3["reply"])
        self.assertEqual(r3["data"]["dish_count"], 5)
        self.assertEqual(r3["data"]["dishes"][0]["course"], "Appetizer")

        # Turn 4: Remove the stuffing
        r4 = _execute_agent_turn("Remove the stuffing", sess, "tr_4")
        self.assertIn("Removed **Traditional Savory Herb Stuffing**", r4["reply"])
        self.assertEqual(r4["data"]["dish_count"], 4)

        # Turn 5: Scale the menu to 12 guests
        r5 = _execute_agent_turn("Scale to 12 guests", sess, "tr_5")
        self.assertEqual(r5["data"]["guest_count"], 12)
        self.assertIn("12 Guests", r5["reply"])

    def test_interactive_non_holiday_event_planning_rename_and_recall(self):
        sess = "test_sess_gamenight"

        # Turn 1: Non-holiday meal planning without courses asks about courses & event label
        r1 = _execute_agent_turn("I need to plan a dinner for 6", sess, "gn_1")
        self.assertIn("6 guests", r1["reply"])
        self.assertIn("Courses & Dishes", r1["reply"])
        self.assertIn("Event Name / Label", r1["reply"])
        self.assertEqual(r1["data"]["status"], "AWAITING_COURSES")

        # Turn 2: User specifies courses
        r2 = _execute_agent_turn(
            "Let's do a stuffed pepper appetizer, an Italian meatballs main course, and chocolate chip cookies for dessert",
            sess,
            "gn_2",
        )
        self.assertEqual(r2["data"]["guest_count"], 6)
        self.assertEqual(r2["data"]["dish_count"], 3)

        # Turn 3: Replace meatballs with grilled chicken + dietary note
        r3 = _execute_agent_turn(
            "One of the folks coming doesn't eat beef. Let's replace the meatballs with some sort of grilled chicken dish.",
            sess,
            "gn_3",
        )
        vibe_calls = [e for e in r3["events"] if e.get("type") == "tool_call" and e.get("tool") == "search_recipes_by_vibe"]
        self.assertEqual(vibe_calls[0]["args"]["query"], "grilled chicken")
        self.assertIn("Dietary Heads-Up", r3["reply"])

        # Turn 4: Rename the event to "Saturday Gamenight"
        r4 = _execute_agent_turn('Okay, let\'s call this event "Saturday Gamenight"', sess, "gn_4")
        self.assertEqual(r4["data"]["name"], "Saturday Gamenight")
        self.assertIn("Saturday Gamenight", r4["reply"])

        # Turn 5: Recall "Saturday Gamenight" in a fresh session
        r5 = _execute_agent_turn("What did we plan for Saturday Gamenight?", "fresh_sess_gn", "gn_5")
        self.assertIn("Recalled saved menu", r5["reply"])
        self.assertEqual(r5["data"]["name"], "Saturday Gamenight")


if __name__ == "__main__":
    unittest.main()
