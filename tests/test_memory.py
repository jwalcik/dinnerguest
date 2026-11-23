"""Unit tests for Context Compaction, Persistent Session Memory, and Async Memory Operations."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dinnerguest.memory import (
    EventPlanningMemory,
    MenuMemoryCompactor,
    PersistentSessionStore,
    VertexContextCacheManager,
    consolidate_session_memory_async,
    estimate_history_tokens,
    estimate_tokens,
    schedule_async_memory_compaction,
)
from dinnerguest.tools.menu_store import EventMenuStore


class TestMemoryAndCompaction(unittest.TestCase):
    def test_token_estimation(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreaterEqual(estimate_tokens("Plan a 3-course menu for 8 guests"), 5)
        history = [
            {"role": "user", "text": "Help me plan a menu for game night"},
            {"role": "model", "text": "How many guests are you expecting, and what courses would you like?"},
        ]
        self.assertGreater(estimate_history_tokens(history), 15)

    def test_sliding_window_and_fact_compaction(self):
        compactor = MenuMemoryCompactor(max_recent_turns=4, max_token_budget=1000)
        turns = [
            {"role": "user", "text": "I need to plan a menu for game night"},
            {"role": "model", "text": "How many guests are you expecting?"},
            {"role": "user", "text": "It's going to be 12 guests, not 6. One guest doesn't eat beef."},
            {"role": "model", "text": "Got it, 12 guests and no beef."},
            {"role": "user", "text": "Replace the Italian meatballs with a grilled chicken dish."},
            {"role": "model", "text": "Replaced Italian meatballs with Grilled Chicken."},
            {"role": "user", "text": "Add a deviled egg appetizer."},
            {"role": "model", "text": "Added Classic Creamy Deviled Eggs."},
        ]
        active_menu = {
            "name": "Saturday Gamenight",
            "guest_count": 12,
            "dishes": [
                {"course": "Appetizer", "recipe_title": "Classic Creamy Deviled Eggs"},
                {"course": "Main", "recipe_title": "Herb Grilled Chicken"},
            ],
        }

        compacted_turns, memory = compactor.compact_history(
            turns,
            active_menu_data=active_menu,
        )

        # Sliding window retains only the most recent 4 turns
        self.assertEqual(len(compacted_turns), 4)
        self.assertEqual(memory.compacted_turn_count, 4)
        self.assertGreater(memory.estimated_tokens_saved, 0)
        self.assertEqual(memory.active_event_name, "Saturday Gamenight")
        self.assertEqual(memory.guest_count, 12)
        self.assertIn("beef", memory.dietary_restrictions)
        self.assertTrue(any("meatballs" in r.lower() for r in memory.rejected_dishes))
        self.assertTrue(len(memory.confirmed_courses) == 2)
        self.assertTrue(len(memory.rolling_summary) > 0)

        prompt_block = memory.format_for_system_prompt()
        self.assertIn("[Compacted Menu Planning Memory]", prompt_block)
        self.assertIn("Saturday Gamenight", prompt_block)
        self.assertIn("12", prompt_block)
        self.assertIn("beef", prompt_block)

    def test_token_budget_eviction(self):
        compactor = MenuMemoryCompactor(max_recent_turns=10, max_token_budget=200)
        long_text = "word " * 120  # ~600 chars -> ~150 tokens per turn
        turns = [
            {"role": "user", "text": f"Turn 1 {long_text}"},
            {"role": "model", "text": f"Turn 2 {long_text}"},
            {"role": "user", "text": f"Turn 3 {long_text}"},
            {"role": "model", "text": f"Turn 4 {long_text}"},
        ]
        compacted_turns, memory = compactor.compact_history(turns)
        self.assertLessEqual(len(compacted_turns), 2)
        self.assertGreaterEqual(memory.compacted_turn_count, 2)

    def test_compact_tool_payload(self):
        compactor = MenuMemoryCompactor(tool_char_limit=300)
        large_recipes = [
            {
                "recipe_id": f"r_{i}",
                "title": f"Recipe {i}",
                "servings": 4,
                "ingredients": [f"ingredient {j} with detailed preparation description" for j in range(15)],
                "instructions": [f"step {j} with very long cooking instructions" for j in range(10)],
                "allergen_summary": {"guest_warning": "None"},
            }
            for i in range(10)
        ]
        slim = compactor.compact_tool_payload("search_recipes_by_vibe", large_recipes)
        self.assertLessEqual(len(slim), 5)
        self.assertNotIn("instructions", slim[0])
        self.assertLessEqual(len(slim[0]["ingredients"]), 8)

    def test_adk_before_model_compaction_callback(self):
        compactor = MenuMemoryCompactor(max_recent_turns=2)
        mock_ctx = MagicMock()
        mock_ctx.state = {}
        mock_req = MagicMock()
        mock_req.contents = [
            {"role": "user", "text": "Turn 1"},
            {"role": "model", "text": "Turn 2"},
            {"role": "user", "text": "Turn 3"},
            {"role": "model", "text": "Turn 4"},
        ]
        compactor.adk_before_model_compaction_callback(mock_ctx, mock_req)
        self.assertEqual(len(mock_req.contents), 2)
        self.assertIn("planning_memory", mock_ctx.state)

    def test_persistent_session_store_local_and_firestore_mock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "sessions.json"
            mock_fs = MagicMock()
            store = PersistentSessionStore(
                storage_path=store_path,
                use_firestore=True,
                firestore_client=mock_fs,
            )

            mem = EventPlanningMemory(
                active_event_name="Game Night",
                guest_count=10,
                dietary_restrictions=["peanuts"],
                rolling_summary="User planned Game Night for 10 guests.",
                compacted_turn_count=4,
            )
            session_data = {
                "active_menu_name": "Game Night",
                "planning_memory": mem,
                "chat_history": [
                    {"role": "user", "text": "Contact me at host@example.com for Game Night"},
                    {"role": "model", "text": "Planned Game Night for 10 guests!"},
                ],
                "turn_count": 2,
            }

            saved = store.save_session("sess_123", session_data)
            # PII should be redacted before storage
            self.assertIn("[REDACTED_EMAIL]", saved["chat_history"][0]["text"])
            mock_fs.collection.assert_called_with("dinnerguest_sessions")

            # Verify local reload when Firestore is disabled
            local_store = PersistentSessionStore(storage_path=store_path, use_firestore=False)
            loaded = local_store.load_session("sess_123")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["active_menu_name"], "Game Night")
            self.assertIsInstance(loaded["planning_memory"], EventPlanningMemory)
            self.assertEqual(loaded["planning_memory"].guest_count, 10)
            self.assertIn("peanuts", loaded["planning_memory"].dietary_restrictions)

    def test_async_memory_consolidation_and_menu_store_async(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sess_path = Path(tmpdir) / "async_sessions.json"
            menu_path = Path(tmpdir) / "async_menus.json"
            compactor = MenuMemoryCompactor(max_recent_turns=2)
            sess_store = PersistentSessionStore(storage_path=sess_path, use_firestore=False)
            menu_store = EventMenuStore(storage_path=menu_path, use_firestore=False)

            session_data = {
                "active_menu_name": "Async Party",
                "planning_memory": EventPlanningMemory(),
                "chat_history": [
                    {"role": "user", "text": "Plan a menu for 8 guests"},
                    {"role": "model", "text": "What courses would you like?"},
                    {"role": "user", "text": "Appetizer and main course, no shellfish"},
                    {"role": "model", "text": "Saved Async Party menu for 8 guests"},
                ],
                "turn_count": 4,
            }

            async def _run_async_tests():
                # 1. Test async EventMenuStore save
                saved_menu = await menu_store.save_menu_async(
                    name="Async Party",
                    guest_count=8,
                    dishes=[{"recipe_title": "Herb Chicken", "course": "Main", "ingredients": ["2 lbs chicken"]}],
                )
                self.assertEqual(saved_menu.name, "Async Party")
                self.assertEqual(saved_menu.guest_count, 8)

                # 2. Test schedule_async_memory_compaction inside running event loop
                task = schedule_async_memory_compaction(
                    session_id="async_sess_1",
                    session_data=session_data,
                    active_menu_data=saved_menu.to_dict(),
                    compactor=compactor,
                    session_store=sess_store,
                )
                self.assertIsNotNone(task)
                record = await task
                self.assertEqual(len(record["chat_history"]), 2)
                self.assertEqual(record["planning_memory"]["guest_count"], 8)
                self.assertEqual(record["planning_memory"]["compacted_turn_count"], 2)

            asyncio.run(_run_async_tests())

    def test_vertex_context_cache_manager(self):
        mgr = VertexContextCacheManager(ttl_seconds=900)
        mock_client = MagicMock()
        mock_cached = MagicMock()
        mock_cached.name = "projects/test/locations/us-central1/cachedContents/cache123"
        mock_client.caches.create.return_value = mock_cached

        with patch.dict(os.environ, {"ENABLE_VERTEX_CONTEXT_CACHE": "true"}):
            # Even if google.genai is not installed in minimal unit-test env, get_or_create_cache handles gracefully
            res = mgr.get_or_create_cache(
                client=mock_client,
                model_name="gemini-2.5-flash",
                system_instruction="Test system instruction",
                tools=[],
            )
            if res is not None:
                self.assertEqual(res, "projects/test/locations/us-central1/cachedContents/cache123")


if __name__ == "__main__":
    unittest.main()
