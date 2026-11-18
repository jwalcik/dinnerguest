"""Google Agent Development Kit (ADK) compatibility layer for Dinnerguest.

Exposes the ADK `Agent` class from `google.adk` when installed, or provides
a fully functional, duck-typed fallback implementation for local development and unit tests.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

try:
    from google.adk.agents.llm_agent import Agent
except ImportError:
    try:
        from google.adk.agents import Agent
    except ImportError:
        try:
            from google.adk import Agent
        except ImportError:
            class Agent:  # type: ignore
                """ADK Agent abstraction for culinary orchestration and sub-agents."""

                def __init__(
                    self,
                    name: str = "",
                    model: Optional[str] = None,
                    description: str = "",
                    instruction: str = "",
                    tools: Optional[List[Callable[..., Any]]] = None,
                    sub_agents: Optional[List[Any]] = None,
                    **kwargs: Any,
                ):
                    self.name = name
                    self.model = model or os.getenv("GEN_MODEL", "gemini-2.5-flash")
                    self.description = description
                    self.instruction = instruction
                    self.tools = tools or []
                    self.sub_agents = sub_agents or []
                    for k, v in kwargs.items():
                        setattr(self, k, v)

                def __repr__(self) -> str:
                    return f"<Agent name={self.name!r} tools={len(self.tools)} sub_agents={len(self.sub_agents)}>"
