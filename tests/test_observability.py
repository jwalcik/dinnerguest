"""Unit tests for Observability, Structured JSON Logging, Distributed Tracing, and PII Redaction."""

import io
import json
import logging
import tempfile
import unittest
from unittest.mock import MagicMock

from dinnerguest.observability import (
    PIIRedactor,
    StructuredJsonFormatter,
    _RECORDED_SPANS,
    get_recorded_spans,
    log_agent_intent,
    log_agent_outcome,
    redact_pii,
    set_trace_context,
    start_span,
    traced_tool_call,
)
from dinnerguest.server import _execute_agent_turn
from dinnerguest.tools.menu_store import EventMenuStore


class TestObservabilityAndTracing(unittest.TestCase):
    def setUp(self):
        _RECORDED_SPANS.set([])
        set_trace_context(trace_id="test_trace_12345", session_id="test_session_abc")

    def test_pii_redaction_regex(self):
        raw_text = (
            "Contact host at chef@example.com or (555) 234-5678. "
            "Backup phone: 555-987-6543, SSN: 123-45-6789, "
            "Card: 4111 2222 3333 4444, Key: AIzaSyDa1234567890abcdefghijklmno."
        )
        scrubbed = redact_pii(raw_text)
        self.assertNotIn("chef@example.com", scrubbed)
        self.assertNotIn("555-987-6543", scrubbed)
        self.assertNotIn("123-45-6789", scrubbed)
        self.assertNotIn("4111 2222 3333 4444", scrubbed)
        self.assertNotIn("AIzaSyDa1234567890abcdefghijklmno", scrubbed)
        self.assertIn("[REDACTED_EMAIL]", scrubbed)
        self.assertIn("[REDACTED_PHONE]", scrubbed)
        self.assertIn("[REDACTED_SSN]", scrubbed)
        self.assertIn("[REDACTED_CREDIT_CARD]", scrubbed)
        self.assertIn("[REDACTED_SECRET]", scrubbed)

    def test_pii_redaction_nested_structures_and_menu_store(self):
        nested = {
            "notes": "Send grocery list to host@dinnerguest.io or call 800-555-1212",
            "guests": ["Alice (alice@test.org)", "Bob"],
        }
        cleaned = redact_pii(nested)
        self.assertEqual(cleaned["notes"], "Send grocery list to [REDACTED_EMAIL] or call [REDACTED_PHONE]")
        self.assertEqual(cleaned["guests"][0], "Alice ([REDACTED_EMAIL])")

        # Verify EventMenuStore scrubs PII before persisting to disk
        with tempfile.TemporaryDirectory() as tmpdir:
            store = EventMenuStore(storage_path=f"{tmpdir}/menus.json")
            saved = store.save_menu(
                name="Party for host@example.com",
                guest_count=4,
                dishes=[{"recipe_title": "Pasta", "ingredients": ["1 lb pasta"]}],
                notes="Call caterer at 555-123-4567",
            )
            self.assertIn("[REDACTED_EMAIL]", saved.name)
            self.assertIn("[REDACTED_PHONE]", saved.notes)

    def test_pii_redactor_cloud_dlp_mock(self):
        redactor = PIIRedactor(use_cloud_dlp=True)
        redactor._project_id = "mock-gcp-project"
        mock_dlp = MagicMock()
        mock_resp = MagicMock()
        mock_resp.item.value = "Deliver to [STREET_ADDRESS]"
        mock_dlp.deidentify_content.return_value = mock_resp
        redactor._dlp_client = mock_dlp

        res = redactor._redact_string_via_cloud_dlp("Deliver to 742 Evergreen Terrace")
        self.assertEqual(res, "Deliver to [STREET_ADDRESS]")
        mock_dlp.deidentify_content.assert_called_once()

    def test_structured_json_formatter(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredJsonFormatter(project_id="test-proj"))
        test_logger = logging.getLogger("dinnerguest.test_json")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.INFO)
        test_logger.propagate = False

        test_logger.info(
            "User email user@domain.com requested menu",
            extra={
                "event_type": "TEST_EVENT",
                "metadata": {"phone": "555-222-3333", "servings": 6},
            },
        )
        output = stream.getvalue().strip()
        parsed = json.loads(output)

        self.assertEqual(parsed["severity"], "INFO")
        self.assertEqual(parsed["event_type"], "TEST_EVENT")
        self.assertEqual(parsed["trace_id"], "test_trace_12345")
        self.assertEqual(parsed["logging.googleapis.com/trace"], "projects/test-proj/traces/test_trace_12345")
        self.assertEqual(parsed["session_id"], "test_session_abc")
        self.assertIn("[REDACTED_EMAIL]", parsed["message"])
        self.assertEqual(parsed["metadata"]["phone"], "[REDACTED_PHONE]")
        self.assertEqual(parsed["metadata"]["servings"], 6)

    def test_intent_vs_outcome_capture_and_traced_tool_call(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredJsonFormatter(project_id="test-proj"))
        audit_logger = logging.getLogger("dinnerguest.audit")
        old_handlers = list(audit_logger.handlers)
        audit_logger.handlers = [handler]

        try:
            with traced_tool_call(
                "scale_recipe_servings",
                "Scale pasta recipe to 8 servings",
                {"recipe_title": "Pasta", "target_servings": 8},
                session_id="sess_99",
                trace_id="trace_99",
            ) as ctx:
                ctx["result_summary"] = {"scale_factor": 2.0}

            lines = [json.loads(line) for line in stream.getvalue().strip().splitlines() if line.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["event_type"], "AGENT_INTENT")
            self.assertEqual(lines[0]["action"], "scale_recipe_servings")
            self.assertIn("Scale pasta recipe", lines[0]["intent"])

            self.assertEqual(lines[1]["event_type"], "AGENT_OUTCOME")
            self.assertEqual(lines[1]["action"], "scale_recipe_servings")
            self.assertEqual(lines[1]["status"], "SUCCESS")
            self.assertEqual(lines[1]["outcome"]["scale_factor"], 2.0)
            self.assertGreaterEqual(lines[1]["duration_ms"], 0.0)
        finally:
            audit_logger.handlers = old_handlers

    def test_distributed_tracing_span_hierarchy(self):
        with start_span("parent_operation", attributes={"step": 1}, trace_id="trace_abc") as parent_span:
            with start_span("child_db_query", attributes={"table": "recipes"}) as child_span:
                self.assertEqual(child_span.parent_span_id, parent_span.span_id)
                self.assertEqual(child_span.trace_id, "trace_abc")

        spans = get_recorded_spans()
        self.assertEqual(len(spans), 2)
        child_rec = next(s for s in spans if s["name"] == "child_db_query")
        parent_rec = next(s for s in spans if s["name"] == "parent_operation")
        self.assertEqual(child_rec["parent_span_id"], parent_rec["span_id"])
        self.assertIsNone(parent_rec["parent_span_id"])

    def test_end_to_end_turn_records_spans(self):
        res = _execute_agent_turn(
            user_msg="Check allergens for: milk, peanuts, and flour",
            session_id="sess_trace_test",
            trace_id="trace_e2e_001",
        )
        self.assertIn("spans", res)
        self.assertGreaterEqual(len(res["spans"]), 2)
        span_names = [s["name"] for s in res["spans"]]
        self.assertIn("agent.execute_turn", span_names)
        self.assertIn("tool.check_allergens_for_ingredients", span_names)


if __name__ == "__main__":
    unittest.main()
