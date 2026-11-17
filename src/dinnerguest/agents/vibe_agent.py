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
    asked_questions: List[str] = field(default_factory=list)
    status: str = "IN_PROGRESS"  # "IN_PROGRESS", "RECOMMENDED", "EXHAUSTED_FALLBACK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_query": self.initial_query,
            "clarifying_turn_count": self.clarifying_turn_count,
            "preferences": self.preferences,
            "conversation_history": self.conversation_history,
            "asked_questions": self.asked_questions,
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
        for p in ["pork", "beef", "chicken", "fish", "shrimp", "tofu", "turkey"]:
            if p in lower:
                state.preferences["protein"] = p
                if "flavor_profile" not in state.preferences:
                    state.preferences["flavor_profile"] = "savory"

        # Check for format clues
        if "noodle" in lower or "noodles" in lower or "pasta" in lower or "ramen" in lower:
            state.preferences["format"] = "noodle"
            if "flavor_profile" not in state.preferences:
                state.preferences["flavor_profile"] = "savory"
        elif "taco" in lower or "tacos" in lower or "tortilla" in lower:
            state.preferences["format"] = "taco"
            if "flavor_profile" not in state.preferences:
                state.preferences["flavor_profile"] = "savory"
        elif "soup" in lower:
            state.preferences["format"] = "soup"
            if "flavor_profile" not in state.preferences:
                state.preferences["flavor_profile"] = "savory"
        elif "skillet" in lower or "stew" in lower:
            state.preferences["format"] = "skillet"
            if "flavor_profile" not in state.preferences:
                state.preferences["flavor_profile"] = "savory"
        elif "cookie" in lower or "cookies" in lower:
            state.preferences["format"] = "cookies"
            if "flavor_profile" not in state.preferences:
                state.preferences["flavor_profile"] = "sweet"
        elif "crisp" in lower or "crumble" in lower or "pie" in lower or "dessert" in lower or "cake" in lower:
            state.preferences["format"] = "crisp"
            if "flavor_profile" not in state.preferences:
                state.preferences["flavor_profile"] = "sweet"

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
        spicy_q = "Would you prefer a spicy taco recipe or a noodle dish?"
        if (
            state.preferences.get("vibe") == "spicy"
            and state.preferences.get("protein") == "pork"
            and "format" not in state.preferences
            and spicy_q not in state.asked_questions
        ):
            state.clarifying_turn_count += 1
            state.asked_questions.append(spicy_q)
            return VibeAgentResponse(
                reply_text=spicy_q,
                is_clarifying_question=True,
                state=state,
            )

        # 3. Specific branching: "Cozy / Cold weather" scenario
        # "Would you like something savory or sweet?"
        cozy_q = "What a great idea! For something cozy, would you prefer something savory or sweet?"
        if (
            state.preferences.get("vibe") == "cozy"
            and "flavor_profile" not in state.preferences
            and "protein" not in state.preferences
            and state.preferences.get("format") not in ["soup", "skillet", "noodle", "taco"]
            and cozy_q not in state.asked_questions
        ):
            state.clarifying_turn_count += 1
            state.asked_questions.append(cozy_q)
            return VibeAgentResponse(
                reply_text=cozy_q,
                is_clarifying_question=True,
                state=state,
            )

        # 4. Specific branching: "Sweet / Dessert" scenario
        sweet_q = "Would you prefer freshly baked cookies or a warm fruit crisp?"
        if (
            state.preferences.get("flavor_profile") == "sweet"
            and "format" not in state.preferences
            and sweet_q not in state.asked_questions
        ):
            state.clarifying_turn_count += 1
            state.asked_questions.append(sweet_q)
            return VibeAgentResponse(
                reply_text=sweet_q,
                is_clarifying_question=True,
                state=state,
            )

        # 5. Search for a matching recipe based on collected preferences
        combined_query = f"{state.initial_query} {' '.join(state.preferences.values())}"
        candidates = self.vibe_tool.search_by_vibe(combined_query, filters=state.preferences, limit=3)
        if not candidates and len(state.preferences) > 1:
            relaxed_filters = {k: v for k, v in state.preferences.items() if k != "format"}
            candidates = self.vibe_tool.search_by_vibe(combined_query, filters=relaxed_filters, limit=3)
        if not candidates:
            candidates = self.vibe_tool.search_by_vibe(combined_query, limit=3)

        if candidates:
            top_recipe = candidates[0]
            state.status = "RECOMMENDED"
            return self._build_recommendation_reply(top_recipe, state)

        # 6. If no matches yet and below limit, ask an open clarifying question
        if state.clarifying_turn_count < MAX_CLARIFYING_QUESTIONS:
            state.clarifying_turn_count += 1
            if "protein" not in state.preferences:
                clarifying_prompt = (
                    "To help narrow down the perfect dish, do you have a preferred protein (like chicken, beef, pork, or vegetarian)?"
                )
            elif "format" not in state.preferences:
                clarifying_prompt = (
                    "Do you have a preferred style in mind, such as a hearty soup, pasta, or a quick skillet dish?"
                )
            else:
                clarifying_prompt = (
                    "Do you have any favorite spices, herbs, or cuisines in mind for this meal?"
                )
            if clarifying_prompt in state.asked_questions:
                clarifying_prompt = "Could you tell me a little more about what ingredients you'd love to include?"
            state.asked_questions.append(clarifying_prompt)
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
