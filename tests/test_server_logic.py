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


if __name__ == "__main__":
    unittest.main()
