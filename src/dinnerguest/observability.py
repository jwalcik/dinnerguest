"""Observability, Structured JSON Logging, Distributed Tracing (OpenTelemetry), and PII Redaction.

Implements the four AgentOps Observability & Tracing pillars:
1. Structured JSON Logging: Cloud Logging-compatible JSON records with trace/span correlation.
2. Intent vs. Outcome Capture: Explicit pre-execution intent and post-execution outcome audit logs.
3. Distributed Tracing: OpenTelemetry span hierarchy linking HTTP request -> agent turn -> DB/vector -> tools.
4. PII Redaction: Active scrubbing of sensitive data (emails, phone numbers, SSNs, credit cards, secrets, addresses)
   via deterministic regex patterns and optional Google Cloud DLP (Sensitive Data Protection) API integration.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

# Context variables for propagating trace, span, and session IDs across call trees
_CURRENT_TRACE_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_trace_id", default=None)
_CURRENT_SPAN_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_span_id", default=None)
_CURRENT_SESSION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_session_id", default=None)
_RECORDED_SPANS: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar("recorded_spans", default=None)

# Attempt to initialize OpenTelemetry SDK if installed
_OTEL_AVAILABLE = False
_OTEL_TRACER = None

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    if not isinstance(otel_trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        # Attach Cloud Trace exporter when running on GCP if available
        if os.getenv("K_SERVICE") or os.getenv("ENABLE_CLOUD_TRACE", "false").lower() == "true":
            try:
                from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
                provider.add_span_processor(SimpleSpanProcessor(CloudTraceSpanExporter()))
            except Exception:
                pass
        otel_trace.set_tracer_provider(provider)

    _OTEL_TRACER = otel_trace.get_tracer("dinnerguest.agentops", "0.1.0")
    _OTEL_AVAILABLE = True
except Exception:
    _OTEL_AVAILABLE = False
    _OTEL_TRACER = None


# ==============================================================================
# 1. PII Redaction Pipeline (Regex + Optional Google Cloud DLP API)
# ==============================================================================

PII_PATTERNS = [
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # US Social Security Numbers (XXX-XX-XXXX)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    # Credit Card Numbers (13-16 digits with optional spaces or dashes)
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[REDACTED_CREDIT_CARD]"),
    # Phone numbers: (XXX) XXX-XXXX, XXX-XXX-XXXX, +1 XXX XXX XXXX (avoiding simple recipe numbers/dates)
    (
        re.compile(
            r"(?:(?:\+?1[\s.-]?)?\(\d{3}\)[\s.-]?\d{3}[\s.-]\d{4}|\b(?:\+?1[\s.-])?\d{3}[-.]\d{3}[-.]\d{4}\b)"
        ),
        "[REDACTED_PHONE]",
    ),
    # API Keys / Bearer Tokens (Google AIza..., OpenAI sk-..., OAuth ya29...)
    (re.compile(r"\b(?:AIza[0-9A-Za-z\-_]{20,}|sk-[0-9A-Za-z]{20,}|ya29\.[0-9A-Za-z\-_]{20,})\b"), "[REDACTED_SECRET]"),
]


class PIIRedactor:
    """Scrubs PII from logs, prompts, and persistent memory before storage.

    Supports deterministic regex scrubbing by default and optional Google Cloud DLP
    (Sensitive Data Protection) API de-identification when enabled.
    """

    def __init__(self, use_cloud_dlp: Optional[bool] = None) -> None:
        if use_cloud_dlp is None:
            use_cloud_dlp = os.getenv("ENABLE_CLOUD_DLP", "false").lower() in ("true", "1")
        self.use_cloud_dlp = use_cloud_dlp
        self._dlp_client: Any = None
        self._project_id = os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")

    def _redact_string_via_cloud_dlp(self, text: str) -> Optional[str]:
        """Uses Google Cloud Sensitive Data Protection (DLP) API if configured and reachable."""
        if not self.use_cloud_dlp or not self._project_id or not text.strip():
            return None
        try:
            if self._dlp_client is None:
                from google.cloud import dlp_v2
                self._dlp_client = dlp_v2.DlpServiceClient()

            parent = f"projects/{self._project_id}/locations/global"
            inspect_config = {
                "info_types": [
                    {"name": "EMAIL_ADDRESS"},
                    {"name": "PHONE_NUMBER"},
                    {"name": "CREDIT_CARD_NUMBER"},
                    {"name": "US_SOCIAL_SECURITY_NUMBER"},
                    {"name": "STREET_ADDRESS"},
                    {"name": "PERSON_NAME"},
                ]
            }
            deidentify_config = {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "primitive_transformation": {
                                "replace_with_info_type_config": {}
                            }
                        }
                    ]
                }
            }
            response = self._dlp_client.deidentify_content(
                request={
                    "parent": parent,
                    "inspect_config": inspect_config,
                    "deidentify_config": deidentify_config,
                    "item": {"value": text},
                }
            )
            return response.item.value
        except Exception:
            return None

    def redact_text(self, text: str) -> str:
        """Redacts sensitive PII tokens from a string."""
        if not text or not isinstance(text, str):
            return text

        # 1. Deterministic regex scrubbing
        scrubbed = text
        for pattern, replacement in PII_PATTERNS:
            scrubbed = pattern.sub(replacement, scrubbed)

        # 2. Optional Cloud DLP pass if enabled
        if self.use_cloud_dlp:
            dlp_res = self._redact_string_via_cloud_dlp(scrubbed)
            if dlp_res is not None:
                scrubbed = dlp_res

        return scrubbed

    def redact(self, data: Any) -> Any:
        """Recursively redacts PII from strings, dictionaries, lists, and tuples."""
        if isinstance(data, str):
            return self.redact_text(data)
        if isinstance(data, dict):
            return {k: self.redact(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.redact(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact(item) for item in data)
        return data


_DEFAULT_REDACTOR = PIIRedactor()


def redact_pii(data: Any) -> Any:
    """Convenience function to scrub PII from any string or nested data structure."""
    return _DEFAULT_REDACTOR.redact(data)


# ==============================================================================
# 2. Structured JSON Logging with Trace & Span Correlation
# ==============================================================================

class StructuredJsonFormatter(logging.Formatter):
    """Formats Python log records as Google Cloud Logging-compatible structured JSON."""

    def __init__(self, project_id: Optional[str] = None) -> None:
        super().__init__()
        self.project_id = project_id or os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "dinnerguest-local"

    def format(self, record: logging.LogRecord) -> str:
        raw_msg = record.getMessage()
        clean_msg = redact_pii(raw_msg)

        trace_id = getattr(record, "trace_id", None) or _CURRENT_TRACE_ID.get()
        span_id = getattr(record, "span_id", None) or _CURRENT_SPAN_ID.get()
        session_id = getattr(record, "session_id", None) or _CURRENT_SESSION_ID.get()

        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "message": clean_msg,
            "logger": record.name,
            "component": getattr(record, "component", record.module),
            "function": record.funcName,
            "line": record.lineno,
        }

        if trace_id:
            payload["logging.googleapis.com/trace"] = f"projects/{self.project_id}/traces/{trace_id}"
            payload["trace_id"] = trace_id
        if span_id:
            payload["logging.googleapis.com/spanId"] = span_id
            payload["span_id"] = span_id
        if session_id:
            payload["session_id"] = session_id

        # Attach structured AgentOps fields if present on the LogRecord
        for extra_key in ("event_type", "action", "intent", "outcome", "status", "duration_ms", "metadata"):
            val = getattr(record, extra_key, None)
            if val is not None:
                payload[extra_key] = redact_pii(val)

        if record.exc_info:
            payload["exception"] = redact_pii(self.formatException(record.exc_info))

        return json.dumps(payload, default=str)


def get_structured_logger(name: str = "dinnerguest") -> logging.Logger:
    """Returns a logger configured with StructuredJsonFormatter (idempotent)."""
    logger = logging.getLogger(name)
    if not any(getattr(h, "_is_dinnerguest_json", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        setattr(handler, "_is_dinnerguest_json", True)
        logger.addHandler(handler)
        logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        logger.propagate = False
    return logger


# ==============================================================================
# 3. Distributed Tracing (OpenTelemetry + Context-Propagated Span Tree)
# ==============================================================================

@dataclass
class TraceSpan:
    """Represents a single distributed trace span with timing, parent linkage, and attributes."""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_time_ns: int = 0
    end_time_ns: int = 0
    duration_ms: float = 0.0
    status: str = "OK"
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "attributes": redact_pii(self.attributes),
        }


def set_trace_context(trace_id: Optional[str], session_id: Optional[str] = None) -> None:
    """Sets the active trace ID and session ID for the current execution context."""
    if trace_id:
        _CURRENT_TRACE_ID.set(trace_id)
    if session_id:
        _CURRENT_SESSION_ID.set(session_id)
    if _RECORDED_SPANS.get() is None:
        _RECORDED_SPANS.set([])


def get_recorded_spans() -> List[Dict[str, Any]]:
    """Returns all distributed trace spans recorded during the current request turn."""
    spans = _RECORDED_SPANS.get()
    return list(spans) if spans else []


@contextmanager
def start_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Generator[TraceSpan, None, None]:
    """Creates a linked distributed trace span (via OpenTelemetry and internal span tree)."""
    if trace_id:
        _CURRENT_TRACE_ID.set(trace_id)
    if session_id:
        _CURRENT_SESSION_ID.set(session_id)

    active_trace_id = _CURRENT_TRACE_ID.get() or uuid.uuid4().hex
    parent_span_id = _CURRENT_SPAN_ID.get()
    new_span_id = uuid.uuid4().hex[:16]

    token_trace = _CURRENT_TRACE_ID.set(active_trace_id)
    token_span = _CURRENT_SPAN_ID.set(new_span_id)

    if _RECORDED_SPANS.get() is None:
        _RECORDED_SPANS.set([])

    clean_attrs = redact_pii(attributes or {})
    span_obj = TraceSpan(
        name=name,
        trace_id=active_trace_id,
        span_id=new_span_id,
        parent_span_id=parent_span_id,
        start_time_ns=time.time_ns(),
        attributes=clean_attrs,
    )

    otel_cm = _OTEL_TRACER.start_as_current_span(name) if (_OTEL_AVAILABLE and _OTEL_TRACER is not None) else None
    otel_span = otel_cm.__enter__() if otel_cm is not None else None
    if otel_span is not None and hasattr(otel_span, "set_attribute"):
        for k, v in clean_attrs.items():
            try:
                otel_span.set_attribute(str(k), str(v) if not isinstance(v, (bool, int, float)) else v)
            except Exception:
                pass

    try:
        yield span_obj
    except Exception as exc:
        span_obj.status = "ERROR"
        span_obj.attributes["error"] = redact_pii(str(exc))
        if otel_span is not None and hasattr(otel_span, "record_exception"):
            try:
                otel_span.record_exception(exc)
            except Exception:
                pass
        raise
    finally:
        span_obj.end_time_ns = time.time_ns()
        span_obj.duration_ms = (span_obj.end_time_ns - span_obj.start_time_ns) / 1_000_000.0
        spans_list = _RECORDED_SPANS.get()
        if spans_list is not None:
            spans_list.append(span_obj.to_dict())
        if otel_cm is not None:
            try:
                otel_cm.__exit__(None, None, None)
            except Exception:
                pass
        _CURRENT_SPAN_ID.reset(token_span)
        _CURRENT_TRACE_ID.reset(token_trace)


# ==============================================================================
# 4. Intent vs. Outcome Capture
# ==============================================================================

_AUDIT_LOGGER = get_structured_logger("dinnerguest.audit")


def log_agent_intent(
    action: str,
    intent_description: str,
    args: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Logs the agent's intended action and input parameters BEFORE execution."""
    target_logger = logger or _AUDIT_LOGGER
    clean_args = redact_pii(args or {})
    clean_intent = redact_pii(intent_description)

    intent_record = {
        "event_type": "AGENT_INTENT",
        "action": action,
        "intent": clean_intent,
        "args": clean_args,
        "trace_id": trace_id or _CURRENT_TRACE_ID.get(),
        "session_id": session_id or _CURRENT_SESSION_ID.get(),
    }
    target_logger.info(
        f"[INTENT] {action}: {clean_intent}",
        extra={
            "event_type": "AGENT_INTENT",
            "action": action,
            "intent": clean_intent,
            "metadata": {"args": clean_args},
            "trace_id": intent_record["trace_id"],
            "session_id": intent_record["session_id"],
        },
    )
    return intent_record


def log_agent_outcome(
    action: str,
    status: str,
    outcome_summary: Any,
    duration_ms: float = 0.0,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Logs the actual outcome, status, and latency AFTER tool or agent execution."""
    target_logger = logger or _AUDIT_LOGGER
    clean_outcome = redact_pii(outcome_summary)

    outcome_record = {
        "event_type": "AGENT_OUTCOME",
        "action": action,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        "outcome": clean_outcome,
        "trace_id": trace_id or _CURRENT_TRACE_ID.get(),
        "session_id": session_id or _CURRENT_SESSION_ID.get(),
    }
    log_fn = target_logger.info if status.upper() == "SUCCESS" else target_logger.error
    log_fn(
        f"[OUTCOME] {action} ({status}) in {duration_ms:.2f}ms",
        extra={
            "event_type": "AGENT_OUTCOME",
            "action": action,
            "status": status,
            "duration_ms": round(duration_ms, 3),
            "outcome": clean_outcome,
            "trace_id": outcome_record["trace_id"],
            "session_id": outcome_record["session_id"],
        },
    )
    return outcome_record


@contextmanager
def traced_tool_call(
    tool_name: str,
    intent_description: str,
    args: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Context manager that wraps a tool execution with:
    1. Pre-execution AGENT_INTENT structured log
    2. OpenTelemetry distributed trace child span (`tool.<tool_name>`)
    3. Post-execution AGENT_OUTCOME structured log with latency and status
    """
    log_agent_intent(
        action=tool_name,
        intent_description=intent_description,
        args=args,
        session_id=session_id,
        trace_id=trace_id,
    )
    t0 = time.perf_counter()
    outcome_holder: Dict[str, Any] = {"result_summary": None}
    with start_span(f"tool.{tool_name}", attributes={"tool.name": tool_name, "tool.args": args or {}}, trace_id=trace_id, session_id=session_id) as span:
        try:
            yield outcome_holder
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            summary = outcome_holder.get("result_summary", "completed")
            span.attributes["tool.status"] = "SUCCESS"
            log_agent_outcome(
                action=tool_name,
                status="SUCCESS",
                outcome_summary=summary,
                duration_ms=elapsed_ms,
                session_id=session_id,
                trace_id=trace_id,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            span.attributes["tool.status"] = "ERROR"
            log_agent_outcome(
                action=tool_name,
                status="ERROR",
                outcome_summary={"error": str(exc)},
                duration_ms=elapsed_ms,
                session_id=session_id,
                trace_id=trace_id,
            )
            raise
