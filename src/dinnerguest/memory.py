"""Context Compaction, Persistent Session Memory, and Async Memory Consolidation.

Implements Category 2 (Context & Memory) capabilities for Dinnerguest and MenuAgent:
1. History Compaction: Token-budget truncation, sliding window management, ADK `before_model_callback`
   compaction, rolling LLM summarization, and Vertex AI Context Caching (`client.caches.create`).
2. Persistent Session State: Firestore-backed (and local JSON fallback) session and episodic
   menu planning memory store with active PII redaction.
3. Async Memory Operations: Non-blocking background tasks (`asyncio.create_task` / `asyncio.to_thread`)
   for memory summarization, compaction, and database persistence to prevent UI blocking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dinnerguest.observability import (
    get_structured_logger,
    log_agent_intent,
    log_agent_outcome,
    redact_pii,
    start_span,
)

logger = get_structured_logger(__name__)

# Default thresholds for history compaction
DEFAULT_MAX_RECENT_TURNS = int(os.getenv("MEMORY_MAX_RECENT_TURNS", "6"))
DEFAULT_MAX_TOKEN_BUDGET = int(os.getenv("MEMORY_MAX_TOKEN_BUDGET", "2400"))
DEFAULT_TOOL_PAYLOAD_CHAR_LIMIT = int(os.getenv("MEMORY_TOOL_CHAR_LIMIT", "1200"))


def estimate_tokens(text: str) -> int:
    """Estimates LLM token count (~4 characters per token heuristic)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _extract_turn_role_and_text(turn: Any) -> Tuple[str, str]:
    """Normalizes either a dict turn {'role': ..., 'text': ...} or a google.genai Content object."""
    if isinstance(turn, dict):
        role = str(turn.get("role", "user"))
        text = str(turn.get("text", "") or turn.get("content", ""))
        if not text and "parts" in turn and isinstance(turn["parts"], list):
            part_texts = []
            for p in turn["parts"]:
                if isinstance(p, dict) and "text" in p:
                    part_texts.append(str(p["text"]))
                elif isinstance(p, str):
                    part_texts.append(p)
            text = " ".join(part_texts)
        return role, text

    role = getattr(turn, "role", "user") or "user"
    parts = getattr(turn, "parts", None) or []
    texts: List[str] = []
    for p in parts:
        t = getattr(p, "text", None)
        if t:
            texts.append(str(t))
    return str(role), " ".join(texts)


def estimate_history_tokens(history: List[Any]) -> int:
    """Computes total estimated tokens across a list of conversation turns."""
    total = 0
    for turn in history or []:
        _, text = _extract_turn_role_and_text(turn)
        total += estimate_tokens(text) + 4  # +4 per-message role framing overhead
    return total


@dataclass
class EventPlanningMemory:
    """Structured episodic memory compacted from multi-turn MenuAgent conversations."""

    active_event_name: Optional[str] = None
    guest_count: Optional[int] = None
    confirmed_courses: List[str] = field(default_factory=list)
    rejected_dishes: List[str] = field(default_factory=list)
    dietary_restrictions: List[str] = field(default_factory=list)
    rolling_summary: str = ""
    compacted_turn_count: int = 0
    estimated_tokens_saved: int = 0
    last_compacted_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_event_name": self.active_event_name,
            "guest_count": self.guest_count,
            "confirmed_courses": list(self.confirmed_courses),
            "rejected_dishes": list(self.rejected_dishes),
            "dietary_restrictions": list(self.dietary_restrictions),
            "rolling_summary": self.rolling_summary,
            "compacted_turn_count": self.compacted_turn_count,
            "estimated_tokens_saved": self.estimated_tokens_saved,
            "last_compacted_at": self.last_compacted_at,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> EventPlanningMemory:
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            active_event_name=data.get("active_event_name"),
            guest_count=data.get("guest_count"),
            confirmed_courses=list(data.get("confirmed_courses") or []),
            rejected_dishes=list(data.get("rejected_dishes") or []),
            dietary_restrictions=list(data.get("dietary_restrictions") or []),
            rolling_summary=str(data.get("rolling_summary") or ""),
            compacted_turn_count=int(data.get("compacted_turn_count") or 0),
            estimated_tokens_saved=int(data.get("estimated_tokens_saved") or 0),
            last_compacted_at=str(data.get("last_compacted_at") or ""),
        )

    def format_for_system_prompt(self) -> str:
        """Formats the compacted planning memory for injection into the LLM system prompt."""
        parts: List[str] = []
        if self.active_event_name:
            parts.append(f"- Active Event Name/Label: {self.active_event_name}")
        if self.guest_count is not None:
            parts.append(f"- Planned Guest Count: {self.guest_count}")
        if self.dietary_restrictions:
            parts.append(f"- Guest Dietary Restrictions / Exclusions: {', '.join(self.dietary_restrictions)}")
        if self.rejected_dishes:
            parts.append(f"- Previously Replaced/Rejected Dishes (Do Not Re-Suggest): {', '.join(self.rejected_dishes)}")
        if self.confirmed_courses:
            parts.append(f"- Confirmed Courses/Dishes: {', '.join(self.confirmed_courses)}")
        if self.rolling_summary:
            parts.append(f"- Compacted Conversation Summary ({self.compacted_turn_count} earlier turns): {self.rolling_summary}")

        if not parts:
            return ""
        return "[Compacted Menu Planning Memory]\n" + "\n".join(parts)


class MenuMemoryCompactor:
    """Manages context bloat for MenuAgent and Dinnerguest via sliding windows,
    token-budget truncation, and LLM-driven rolling summarization.
    """

    def __init__(
        self,
        max_recent_turns: int = DEFAULT_MAX_RECENT_TURNS,
        max_token_budget: int = DEFAULT_MAX_TOKEN_BUDGET,
        tool_char_limit: int = DEFAULT_TOOL_PAYLOAD_CHAR_LIMIT,
    ) -> None:
        self.max_recent_turns = max(2, max_recent_turns)
        self.max_token_budget = max(200, max_token_budget)
        self.tool_char_limit = max(200, tool_char_limit)

    def compact_tool_payload(self, tool_name: str, tool_result: Any) -> Any:
        """Truncates bulky intermediate tool outputs (e.g. long recipe candidate arrays)
        to prevent context window bloat across multi-turn menu planning.
        """
        if not isinstance(tool_result, (dict, list)):
            return tool_result

        serialized = json.dumps(tool_result, default=str)
        if len(serialized) <= self.tool_char_limit:
            return tool_result

        if isinstance(tool_result, list):
            compacted_list = []
            for item in tool_result[:5]:
                if isinstance(item, dict):
                    compacted_list.append({
                        "recipe_id": item.get("recipe_id") or item.get("id"),
                        "title": item.get("title") or item.get("recipe_title"),
                        "servings": item.get("servings"),
                        "ingredients": (item.get("ingredients") or [])[:8],
                        "allergen_summary": item.get("allergen_summary"),
                    })
                else:
                    compacted_list.append(item)
            return compacted_list

        if isinstance(tool_result, dict) and "recipes" in tool_result and isinstance(tool_result["recipes"], list):
            slim = dict(tool_result)
            slim["recipes"] = self.compact_tool_payload(tool_name, tool_result["recipes"])
            return slim

        return tool_result

    def extract_menu_facts_into_memory(
        self,
        turns: List[Tuple[str, str]],
        memory: EventPlanningMemory,
        active_menu_data: Optional[Dict[str, Any]] = None,
    ) -> EventPlanningMemory:
        """Extracts key menu planning state (guest count, dietary constraints, swapped dishes,
        and event labels) from conversation turns and active menu data into EventPlanningMemory.
        """
        if active_menu_data and isinstance(active_menu_data, dict):
            if active_menu_data.get("name"):
                memory.active_event_name = str(active_menu_data["name"])
            if active_menu_data.get("guest_count"):
                memory.guest_count = int(active_menu_data["guest_count"])
            dishes = active_menu_data.get("dishes")
            if isinstance(dishes, list) and dishes:
                courses = []
                for d in dishes:
                    if isinstance(d, dict):
                        c_name = d.get("course", "Course")
                        r_title = d.get("recipe_title") or d.get("title") or "Dish"
                        courses.append(f"{c_name}: {r_title}")
                if courses:
                    memory.confirmed_courses = courses

        for role, text in turns:
            clean_text = redact_pii(text.strip())
            if not clean_text:
                continue
            lower = clean_text.lower()

            if role == "user":
                # Extract dietary restrictions / exclusions mentioned by user
                diet_patterns = [
                    r"\b(?:doesn't|does\s+not|don't|do\s+not)\s+(?:eat|have|want)\s+(?:any\s+)?([a-zA-Z\s]{3,20}?)(?=[.,;?!]|$|\band\b|\bso\b|\blet's\b)",
                    r"\b(?:allergic\s+to|allergy\s+to|avoid|without|no)\s+([a-zA-Z\s]{3,20}?)(?=[.,;?!]|$|\band\b|\bso\b)",
                    r"\b(vegetarian|vegan|gluten-free|dairy-free|nut-free|kosher|halal)\b",
                ]
                for pat in diet_patterns:
                    for m in re.finditer(pat, lower):
                        restriction = m.group(1).strip()
                        if restriction and len(restriction) >= 3 and restriction not in memory.dietary_restrictions:
                            memory.dietary_restrictions.append(restriction)

                # Extract dishes the user explicitly replaced or removed
                rep_m = re.search(
                    r"\b(?:replace|swap|switch|remove|drop)\s+(?:the\s+|our\s+)?([a-zA-Z\s]{3,30}?)(?:\s+with\s+|\s+for\s+|\s+from\s+|[.,;?!]|$)",
                    clean_text,
                    flags=re.IGNORECASE,
                )
                if rep_m:
                    rejected = rep_m.group(1).strip()
                    if rejected and rejected.lower() not in {r.lower() for r in memory.rejected_dishes}:
                        memory.rejected_dishes.append(rejected)

                # Extract guest count updates (ignoring negated 'not 6')
                no_neg = re.sub(r"\b(?:not|instead\s+of)\s+\d+(?:\s*guests?)?\b", "", lower)
                gc_m = re.search(r"\b(\d+)\s*(?:guests|people|folks|friends)\b", no_neg)
                if gc_m and int(gc_m.group(1)) > 0:
                    memory.guest_count = int(gc_m.group(1))

        return memory

    def _summarize_turns_with_llm_or_fallback(
        self,
        older_turns: List[Tuple[str, str]],
        existing_summary: str,
    ) -> str:
        """Summarizes older conversation turns using Vertex AI Gemini if available,
        or produces a concise structured digest offline.
        """
        transcript_lines = [f"{role.upper()}: {redact_pii(text)}" for role, text in older_turns if text.strip()]
        if not transcript_lines:
            return existing_summary

        project = os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        if project and os.getenv("DISABLE_LLM_AGENT", "false").lower() != "true":
            try:
                from google import genai

                location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
                client = genai.Client(vertexai=True, project=project, location=location)
                prompt = (
                    "Summarize the following earlier culinary/menu-planning conversation turns into a concise "
                    "2-3 sentence memory digest preserving event names, guest counts, course choices, swapped dishes, "
                    "and dietary restrictions.\n\n"
                )
                if existing_summary:
                    prompt += f"Previous Summary: {existing_summary}\n\n"
                prompt += "Turns to Compact:\n" + "\n".join(transcript_lines)

                res = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                llm_summary = (getattr(res, "text", "") or "").strip()
                if llm_summary:
                    return redact_pii(llm_summary)
            except Exception as exc:
                logger.debug("LLM memory summarization fallback to deterministic digest: %s", exc)

        # Deterministic concise digest fallback
        snippets: List[str] = []
        if existing_summary:
            snippets.append(existing_summary)
        for role, text in older_turns:
            clean = redact_pii(re.sub(r"\s+", " ", text).strip())
            if not clean:
                continue
            if len(clean) > 120:
                clean = clean[:117] + "..."
            snippets.append(f"{role}: {clean}")

        combined = " | ".join(snippets)
        if len(combined) > 600:
            combined = "..." + combined[-597:]
        return combined

    def compact_history(
        self,
        history: List[Any],
        memory: Optional[EventPlanningMemory] = None,
        active_menu_data: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Tuple[List[Dict[str, str]], EventPlanningMemory]:
        """Compacts conversation history when turn count or token budget exceeds thresholds.

        Returns:
            Tuple of (compacted_recent_turns_as_dicts, updated_EventPlanningMemory).
        """
        if memory is None:
            memory = EventPlanningMemory()

        normalized_turns: List[Tuple[str, str]] = []
        for t in history or []:
            role, text = _extract_turn_role_and_text(t)
            if text.strip():
                normalized_turns.append((role, redact_pii(text.strip())))

        # Always keep structured facts updated from all turns
        self.extract_menu_facts_into_memory(normalized_turns, memory, active_menu_data=active_menu_data)

        total_tokens_before = sum(estimate_tokens(txt) + 4 for _, txt in normalized_turns)
        needs_compaction = (
            force
            or len(normalized_turns) > self.max_recent_turns
            or total_tokens_before > self.max_token_budget
        )

        if not needs_compaction:
            return ([{"role": r, "text": txt} for r, txt in normalized_turns], memory)

        with start_span(
            "memory.compact_history",
            attributes={
                "turns_before": len(normalized_turns),
                "tokens_before": total_tokens_before,
                "max_recent_turns": self.max_recent_turns,
                "max_token_budget": self.max_token_budget,
            },
        ):
            # Split into older turns to summarize and recent turns to keep in sliding window
            keep_count = min(len(normalized_turns), self.max_recent_turns)
            older_turns = normalized_turns[:-keep_count] if keep_count > 0 else list(normalized_turns)
            recent_turns = normalized_turns[-keep_count:] if keep_count > 0 else []

            # Further trim recent_turns if they still exceed max_token_budget
            while len(recent_turns) > 2 and sum(estimate_tokens(t) + 4 for _, t in recent_turns) > self.max_token_budget:
                evicted = recent_turns.pop(0)
                older_turns.append(evicted)

            if older_turns:
                memory.rolling_summary = self._summarize_turns_with_llm_or_fallback(
                    older_turns,
                    memory.rolling_summary,
                )
                memory.compacted_turn_count += len(older_turns)
                tokens_after = sum(estimate_tokens(t) + 4 for _, t in recent_turns) + estimate_tokens(memory.rolling_summary)
                memory.estimated_tokens_saved += max(0, total_tokens_before - tokens_after)
                memory.last_compacted_at = datetime.now(timezone.utc).isoformat()

            compacted_dicts = [{"role": r, "text": txt} for r, txt in recent_turns]
            return compacted_dicts, memory

    def adk_before_model_compaction_callback(
        self,
        callback_context: Any,
        llm_request: Any,
    ) -> Optional[Any]:
        """ADK-compatible `before_model_callback` hook that compacts `llm_request.contents`
        when the conversation history exceeds the token or turn budget.
        """
        try:
            contents = getattr(llm_request, "contents", None)
            if not contents or len(contents) <= self.max_recent_turns:
                return None

            state_dict = getattr(callback_context, "state", {}) if callback_context else {}
            existing_mem = EventPlanningMemory.from_dict(state_dict.get("planning_memory") if isinstance(state_dict, dict) else None)
            compacted_turns, updated_mem = self.compact_history(contents, memory=existing_mem)
            if isinstance(state_dict, dict):
                state_dict["planning_memory"] = updated_mem.to_dict()

            # Keep only the most recent `len(compacted_turns)` items in llm_request.contents
            if len(compacted_turns) < len(contents):
                llm_request.contents = list(contents[-len(compacted_turns):])
        except Exception as exc:
            logger.debug("ADK before_model_callback compaction skipped: %s", exc)
        return None


class VertexContextCacheManager:
    """Manages Google Cloud Vertex AI Context Caching (`client.caches.create`) for
    static agent instructions and tool declarations to reduce token latency and cost.
    """

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache_registry: Dict[str, str] = {}
        self._lock = threading.Lock()

    def get_or_create_cache(
        self,
        client: Any,
        model_name: str,
        system_instruction: str,
        tools: Optional[List[Any]] = None,
    ) -> Optional[str]:
        """Creates or reuses a Vertex AI CachedContent resource if enabled and supported."""
        if os.getenv("ENABLE_VERTEX_CONTEXT_CACHE", "false").lower() not in ("true", "1"):
            return None

        fingerprint = hashlib.sha256(f"{model_name}:{system_instruction}".encode("utf-8")).hexdigest()[:16]
        with self._lock:
            if fingerprint in self._cache_registry:
                return self._cache_registry[fingerprint]

        try:
            from google.genai import types

            cache_config = types.CreateCachedContentConfig(
                system_instruction=system_instruction,
                tools=tools,
                ttl=f"{self.ttl_seconds}s",
                display_name=f"dinnerguest-ctx-{fingerprint}",
            )
            cached = client.caches.create(model=model_name, config=cache_config)
            cache_name = getattr(cached, "name", None)
            if cache_name:
                with self._lock:
                    self._cache_registry[fingerprint] = str(cache_name)
                return str(cache_name)
        except Exception as exc:
            logger.debug("Vertex AI Context Cache creation skipped (%s)", exc)
        return None


class PersistentSessionStore:
    """Persistent store for multi-turn conversation history and compacted EventPlanningMemory.

    Connects to Google Cloud Firestore (`dinnerguest_sessions` collection) when enabled,
    falling back seamlessly to local JSON storage for local development and tests.
    """

    def __init__(
        self,
        storage_path: Optional[str | Path] = None,
        use_firestore: Optional[bool] = None,
        firestore_client: Any = None,
    ) -> None:
        if storage_path:
            self.storage_path = Path(storage_path)
        elif os.getenv("SESSION_STORAGE_PATH"):
            self.storage_path = Path(os.environ["SESSION_STORAGE_PATH"])
        else:
            self.storage_path = Path("/tmp/dinnerguest_sessions.json")

        if use_firestore is None:
            use_firestore = os.getenv("USE_FIRESTORE", "false").lower() in ("true", "1")
        self.use_firestore = use_firestore
        self.firestore_collection = os.getenv("FIRESTORE_SESSIONS_COLLECTION", "dinnerguest_sessions")
        self._firestore_db = firestore_client
        self._lock = threading.Lock()

        if self.use_firestore and self._firestore_db is None:
            self._init_firestore()

    def _init_firestore(self) -> None:
        try:
            from google.cloud import firestore

            project = os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
            database = os.getenv("FIRESTORE_DATABASE", "dinnerguest-events")
            self._firestore_db = firestore.Client(project=project, database=database)
        except Exception as exc:
            logger.warning("Firestore session store unavailable (%s); using local file %s", exc, self.storage_path)
            self.use_firestore = False
            self._firestore_db = None

    def _serialize_session(self, session_id: str, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Serializes a session dict into a JSON-safe, PII-redacted record."""
        raw_history = session_data.get("chat_history") or []
        serialized_turns: List[Dict[str, str]] = []
        for item in raw_history:
            role, text = _extract_turn_role_and_text(item)
            if text.strip():
                serialized_turns.append({
                    "role": role,
                    "text": redact_pii(text.strip()),
                })

        planning_mem = session_data.get("planning_memory")
        if isinstance(planning_mem, EventPlanningMemory):
            planning_mem_dict = planning_mem.to_dict()
        elif isinstance(planning_mem, dict):
            planning_mem_dict = planning_mem
        else:
            planning_mem_dict = EventPlanningMemory().to_dict()

        return {
            "session_id": session_id,
            "active_menu_name": redact_pii(str(session_data["active_menu_name"])) if session_data.get("active_menu_name") else None,
            "menu_planning_state": session_data.get("menu_planning_state"),
            "planning_memory": planning_mem_dict,
            "chat_history": serialized_turns,
            "turn_count": int(session_data.get("turn_count") or 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def save_session(self, session_id: str, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Persists a session state record to Firestore and/or local JSON storage."""
        record = self._serialize_session(session_id, session_data)

        if self.use_firestore and self._firestore_db is not None:
            try:
                self._firestore_db.collection(self.firestore_collection).document(session_id).set(record)
            except Exception as exc:
                logger.warning("Error saving session '%s' to Firestore: %s", session_id, exc)

        with self._lock:
            all_sessions = self._load_local_file()
            all_sessions[session_id] = record
            try:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump(all_sessions, f, indent=2)
            except OSError as exc:
                logger.warning("Could not write local session store %s: %s", self.storage_path, exc)

        return record

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Loads a persisted session from Firestore or local JSON storage."""
        if self.use_firestore and self._firestore_db is not None:
            try:
                doc = self._firestore_db.collection(self.firestore_collection).document(session_id).get()
                if doc.exists:
                    data = doc.to_dict() or {}
                    data["planning_memory"] = EventPlanningMemory.from_dict(data.get("planning_memory"))
                    return data
            except Exception as exc:
                logger.warning("Error reading session '%s' from Firestore: %s", session_id, exc)

        with self._lock:
            all_sessions = self._load_local_file()
            data = all_sessions.get(session_id)
            if data:
                loaded = dict(data)
                loaded["planning_memory"] = EventPlanningMemory.from_dict(loaded.get("planning_memory"))
                return loaded
        return None

    def _load_local_file(self) -> Dict[str, Dict[str, Any]]:
        if not self.storage_path.exists():
            return {}
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}


# Global singletons for compactor, persistent session store, and Vertex context cache manager
_DEFAULT_COMPACTOR = MenuMemoryCompactor()
_DEFAULT_SESSION_STORE = PersistentSessionStore()
_DEFAULT_CONTEXT_CACHE = VertexContextCacheManager()
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def consolidate_session_memory_async(
    session_id: str,
    session_data: Dict[str, Any],
    active_menu_data: Optional[Dict[str, Any]] = None,
    compactor: Optional[MenuMemoryCompactor] = None,
    session_store: Optional[PersistentSessionStore] = None,
    force_compaction: bool = False,
) -> Dict[str, Any]:
    """Asynchronously compacts conversation history, updates EventPlanningMemory,
    and persists session state to Firestore/disk without blocking the UI thread.
    """
    comp = compactor or _DEFAULT_COMPACTOR
    store = session_store or _DEFAULT_SESSION_STORE

    def _run_consolidation() -> Dict[str, Any]:
        log_agent_intent(
            action="memory.consolidate_async",
            intent_description=f"Background memory compaction and persistence for session '{session_id}'",
            args={"session_id": session_id, "turn_count": session_data.get("turn_count", 0)},
            session_id=session_id,
        )
        raw_history = session_data.get("chat_history") or []
        mem_obj = session_data.get("planning_memory")
        if isinstance(mem_obj, dict):
            mem_obj = EventPlanningMemory.from_dict(mem_obj)
        elif not isinstance(mem_obj, EventPlanningMemory):
            mem_obj = EventPlanningMemory()

        compacted_turns, updated_mem = comp.compact_history(
            raw_history,
            memory=mem_obj,
            active_menu_data=active_menu_data,
            force=force_compaction,
        )
        session_data["chat_history"] = compacted_turns
        session_data["planning_memory"] = updated_mem
        saved_record = store.save_session(session_id, session_data)
        log_agent_outcome(
            action="memory.consolidate_async",
            status="SUCCESS",
            outcome_summary={
                "retained_turns": len(compacted_turns),
                "compacted_turn_count": updated_mem.compacted_turn_count,
                "estimated_tokens_saved": updated_mem.estimated_tokens_saved,
            },
            session_id=session_id,
        )
        return saved_record

    return await asyncio.to_thread(_run_consolidation)


def schedule_async_memory_compaction(
    session_id: str,
    session_data: Dict[str, Any],
    active_menu_data: Optional[Dict[str, Any]] = None,
    compactor: Optional[MenuMemoryCompactor] = None,
    session_store: Optional[PersistentSessionStore] = None,
) -> Optional[asyncio.Task[Any]]:
    """Schedules non-blocking background memory compaction and persistence.

    If an asyncio event loop is running (e.g., during `/chat/stream`), schedules an
    `asyncio.Task` and returns immediately so the UI is never blocked. Otherwise
    spawns a daemon background thread.
    """
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            consolidate_session_memory_async(
                session_id=session_id,
                session_data=session_data,
                active_menu_data=active_menu_data,
                compactor=compactor,
                session_store=session_store,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task
    except RuntimeError:
        # No running asyncio loop (e.g., synchronous caller); run on a daemon thread
        def _bg_worker() -> None:
            try:
                asyncio.run(
                    consolidate_session_memory_async(
                        session_id=session_id,
                        session_data=session_data,
                        active_menu_data=active_menu_data,
                        compactor=compactor,
                        session_store=session_store,
                    )
                )
            except Exception as exc:
                logger.debug("Background memory thread skipped: %s", exc)

        t = threading.Thread(target=_bg_worker, daemon=True)
        t.start()
        return None
