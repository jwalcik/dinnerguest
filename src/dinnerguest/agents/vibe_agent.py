"""Vibe & Mood Recommendations Sub-Agent.

Conducts interactive clarifying dialogue (up to 5 questions) to narrow down
recipes by mood, flavor, and cravings, enforcing graceful fallback upon limit exhaustion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dinnerguest.tools.vibe_search_tool import VibeRecipeResult, VibeSearchTool


MAX_CLARIFYING_QUESTIONS = 5


@dataclass
class VibeDialogueState:
    initial_query: str = ""
    clarifying_turn_count: int = 0
    preferences: Dict[str, str] = field(default_factory=dict)
    conversation_history: List[str] = field(default_factory=list)
    status: str = "IN_PROGRESS"  # "IN_PROGRESS", "RECOMMENDED", "EXHAUSTED_FALLBACK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_query": self.initial_query,
            "clarifying_turn_count": self.clarifying_turn_count,
            "preferences": self.preferences,
            "conversation_history": self.conversation_history,
            "status": self.status,
        }


@dataclass
class VibeAgentResponse:
    reply_text: str
    is_clarifying_question: bool
    state: VibeDialogueState
    recommended_recipes: List[VibeRecipeResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply_text": self.reply_text,
            "is_clarifying_question": self.is_clarifying_question,
            "state": self.state.to_dict(),
            "recommended_recipes": [r.to_dict() for r in self.recommended_recipes],
        }


class VibeRecommendationAgent:
    """Sub-agent managing mood-based recommendation dialogues with strict 5-turn guardrails."""

    def __init__(self, vibe_tool: Optional[VibeSearchTool] = None):
        self.vibe_tool = vibe_tool or VibeSearchTool()

    def _extract_intent_clues(self, text: str, state: VibeDialogueState) -> None:
        """Parses user input for flavor profiles, protein types, dish formats, and keywords."""
        lower = text.lower()

        # Check for protein clues
        for p in ["pork", "beef", "chicken", "fish", "shrimp", "tofu"]:
            if p in lower:
                state.preferences["protein"] = p

        # Check for format clues
        if "noodle" in lower or "noodles" in lower or "pasta" in lower or "ramen" in lower:
            state.preferences["format"] = "noodle"
        elif "taco" in lower or "tacos" in lower or "tortilla" in lower:
            state.preferences["format"] = "taco"
        elif "skillet" in lower or "stew" in lower or "soup" in lower:
            state.preferences["format"] = "skillet"
        elif "cookie" in lower or "cookies" in lower:
            state.preferences["format"] = "cookies"
        elif "crisp" in lower or "crumble" in lower or "pie" in lower:
            state.preferences["format"] = "crisp"

        # Check for flavor profile clues
        if "savory" in lower:
            state.preferences["flavor_profile"] = "savory"
        elif "sweet" in lower:
            state.preferences["flavor_profile"] = "sweet"
        elif "spicy" in lower or "hot" in lower:
            state.preferences["vibe"] = "spicy"
        elif "cozy" in lower or "cold" in lower or "comfort" in lower or "winter" in lower or "chilly" in lower:
            state.preferences["vibe"] = "cozy"

    def handle_message(
        self,
        user_message: str,
        state: Optional[VibeDialogueState] = None,
    ) -> VibeAgentResponse:
        """Processes a conversation turn, either asking a clarifying question or recommending a recipe."""
        if state is None:
            state = VibeDialogueState(initial_query=user_message)
        state.conversation_history.append(user_message)

        self._extract_intent_clues(user_message, state)

        # 1. Enforce hard 5-question limit
        if state.clarifying_turn_count >= MAX_CLARIFYING_QUESTIONS:
            # Attempt a final fallback search with what we have
            combined_query = f"{state.initial_query} {' '.join(state.preferences.values())}"
            candidates = self.vibe_tool.search_by_vibe(combined_query, filters=state.preferences, limit=1)

            if candidates:
                rec = candidates[0]
                state.status = "RECOMMENDED"
                return self._build_recommendation_reply(rec, state)
            else:
                state.status = "EXHAUSTED_FALLBACK"
                apology = (
                    "I apologize, but after asking several questions I haven't been able to identify "
                    "a specific recommendation that fits what you're looking for. "
                    "Could you please provide more details in your request (such as key ingredients, favorite cuisine, or dietary preferences)?"
                )
                return VibeAgentResponse(
                    reply_text=apology,
                    is_clarifying_question=False,
                    state=state,
                    recommended_recipes=[],
                )

        # 2. Specific branching: "Spicy Pork" scenario
        # "Would you prefer a spicy taco recipe or a noodle dish?"
        if (
            state.preferences.get("vibe") == "spicy"
            and state.preferences.get("protein") == "pork"
            and "format" not in state.preferences
        ):
            state.clarifying_turn_count += 1
            question = "Would you prefer a spicy taco recipe or a noodle dish?"
            return VibeAgentResponse(
                reply_text=question,
                is_clarifying_question=True,
                state=state,
            )

        # 3. Specific branching: "Cozy / Cold weather" scenario
        # "Would you like something savory or sweet?"
        if state.preferences.get("vibe") == "cozy" and "flavor_profile" not in state.preferences:
            state.clarifying_turn_count += 1
            question = "What a great idea! For something cozy, would you prefer something savory or sweet?"
            return VibeAgentResponse(
                reply_text=question,
                is_clarifying_question=True,
                state=state,
            )

        # 4. Specific branching: "Sweet / Dessert" scenario
        if state.preferences.get("flavor_profile") == "sweet" and "format" not in state.preferences:
            state.clarifying_turn_count += 1
            question = "Would you prefer freshly baked cookies or a warm fruit crisp?"
            return VibeAgentResponse(
                reply_text=question,
                is_clarifying_question=True,
                state=state,
            )

        # 5. Search for a matching recipe based on collected preferences
        combined_query = f"{state.initial_query} {' '.join(state.preferences.values())}"
        candidates = self.vibe_tool.search_by_vibe(combined_query, filters=state.preferences, limit=3)

        if candidates:
            top_recipe = candidates[0]
            state.status = "RECOMMENDED"
            return self._build_recommendation_reply(top_recipe, state)

        # 6. If no matches yet and below limit, ask an open clarifying question
        if state.clarifying_turn_count < MAX_CLARIFYING_QUESTIONS:
            state.clarifying_turn_count += 1
            clarifying_prompt = (
                f"To help narrow down the perfect dish, do you have a preferred protein (like beef, pork, or vegetarian) "
                f"or a favorite cuisine in mind?"
            )
            return VibeAgentResponse(
                reply_text=clarifying_prompt,
                is_clarifying_question=True,
                state=state,
            )

        # 7. Fallback if turn limit reached without match
        state.status = "EXHAUSTED_FALLBACK"
        apology = (
            "I apologize for not being able to find a good option after a few questions. "
            "Could you please provide more details in your request?"
        )
        return VibeAgentResponse(
            reply_text=apology,
            is_clarifying_question=False,
            state=state,
            recommended_recipes=[],
        )

    def _build_recommendation_reply(
        self,
        recipe: VibeRecipeResult,
        state: VibeDialogueState,
    ) -> VibeAgentResponse:
        """Formats the final recipe recommendation with allergen notices."""
        lines = [
            f"Here is a great recipe recommendation for you:\n",
            f"## {recipe.title}",
            f"- **Yield**: Serves {recipe.servings}",
            f"- **Time**: {recipe.prep_time_minutes} min prep, {recipe.cook_time_minutes} min cook",
            f"- **Vibe & Tags**: {', '.join(recipe.tags)}",
            "",
            "### Ingredients:",
        ]
        for ing in recipe.ingredients:
            lines.append(f"- {ing}")

        lines.append("\n### Instructions:")
        for idx, step in enumerate(recipe.instructions, 1):
            lines.append(f"{idx}. {step}")

        if recipe.allergen_report:
            lines.append(f"\n⚠️ **Allergen Notice**: {recipe.allergen_report.get_guest_warning_summary()}")

        return VibeAgentResponse(
            reply_text="\n".join(lines),
            is_clarifying_question=False,
            state=state,
            recommended_recipes=[recipe],
        )
