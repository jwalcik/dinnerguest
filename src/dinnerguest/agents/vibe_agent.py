"""Vibe & Mood Recommendations Sub-Agent.

Conducts interactive clarifying dialogue (up to 5 questions) to narrow down
recipes by mood, flavor, and cravings, enforcing graceful fallback upon limit exhaustion.
Powered by vector RAG similarity search and registered as an ADK sub-agent.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dinnerguest.adk import Agent
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
        """Parses user input for general flavor profiles and style cues dynamically without static ingredient lists."""
        lower = text.lower()
        words = set(re.findall(r"\b\w+\b", lower))

        # Check for flavor profile clues
        if "savory" in words or "umami" in words:
            state.preferences["flavor_profile"] = "savory"
        elif "sweet" in words or "dessert" in words:
            state.preferences["flavor_profile"] = "sweet"
        elif "spicy" in words or "hot" in words:
            state.preferences["vibe"] = "spicy"
        elif any(w in words for w in ["cozy", "cold", "comfort", "winter", "chilly"]):
            state.preferences["vibe"] = "cozy"

        # Check for format clues
        for fmt in ["noodle", "noodles", "taco", "tacos", "soup", "skillet", "stew", "cookie", "cookies", "crisp", "crumble", "salad", "roast", "pasta"]:
            if fmt in words:
                state.preferences["format"] = fmt
                break

    def handle_message(
        self,
        user_message: str,
        state: Optional[VibeDialogueState] = None,
    ) -> VibeAgentResponse:
        """Processes a conversation turn, either asking a clarifying question or recommending a recipe."""
        if state is None:
            state = VibeDialogueState(initial_query=user_message)
        state.conversation_history.append(user_message)

        lower = user_message.lower()
        self._extract_intent_clues(user_message, state)

        # 1. Enforce hard 5-question limit guardrail
        if state.clarifying_turn_count >= MAX_CLARIFYING_QUESTIONS:
            combined_query = " ".join(state.conversation_history)
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
        has_spicy = "spicy" in lower or state.preferences.get("vibe") == "spicy"
        has_pork = "pork" in lower
        has_format = any(w in lower for w in ["taco", "tacos", "noodle", "noodles", "ramen", "pasta"]) or "format" in state.preferences
        if has_spicy and has_pork and not has_format and spicy_q not in state.asked_questions:
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
        has_cozy = "cozy" in lower or state.preferences.get("vibe") == "cozy"
        has_flv = "flavor_profile" in state.preferences or any(w in lower for w in ["savory", "sweet"])
        has_specific_dish = any(w in lower for w in ["soup", "skillet", "noodle", "stew", "crisp", "cookie", "cake", "pie"])
        if has_cozy and not has_flv and not has_specific_dish and cozy_q not in state.asked_questions:
            state.clarifying_turn_count += 1
            state.asked_questions.append(cozy_q)
            return VibeAgentResponse(
                reply_text=cozy_q,
                is_clarifying_question=True,
                state=state,
            )

        # 4. Specific branching: "Sweet / Dessert" scenario
        sweet_q = "Would you prefer freshly baked cookies or a warm fruit crisp?"
        has_sweet = state.preferences.get("flavor_profile") == "sweet" or "sweet" in lower or "dessert" in lower
        has_sweet_fmt = any(w in lower for w in ["cookie", "cookies", "crisp", "crumble", "pie", "cake"]) or "format" in state.preferences
        if has_sweet and not has_sweet_fmt and sweet_q not in state.asked_questions:
            state.clarifying_turn_count += 1
            state.asked_questions.append(sweet_q)
            return VibeAgentResponse(
                reply_text=sweet_q,
                is_clarifying_question=True,
                state=state,
            )

        # 5. Search for matching recipe based on dialogue context via vector / token search
        combined_query = " ".join(state.conversation_history)
        candidates = self.vibe_tool.search_by_vibe(combined_query, filters=state.preferences, limit=3)

        if candidates:
            top_recipe = candidates[0]
            state.status = "RECOMMENDED"
            return self._build_recommendation_reply(top_recipe, state)

        # 6. If no matches yet and below limit, ask a clarifying question
        if state.clarifying_turn_count < MAX_CLARIFYING_QUESTIONS:
            state.clarifying_turn_count += 1
            clarifying_prompt = "Could you tell me a little more about what ingredients, cuisine, or flavors you'd love to include?"
            if state.clarifying_turn_count == 1:
                clarifying_prompt = (
                    "To help narrow down the perfect dish, do you have a preferred protein (like chicken, beef, seafood, or vegetarian)?"
                )
            elif state.clarifying_turn_count == 2:
                clarifying_prompt = (
                    "Do you have a preferred style in mind, such as a hearty soup, pasta, or a quick skillet dish?"
                )
            if clarifying_prompt in state.asked_questions:
                clarifying_prompt = "Do you have any favorite spices, herbs, or cuisines in mind for this meal?"
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


# ADK Agent Definition
VIBE_AGENT_INSTRUCTION = """You are the Vibe & Mood Recommendation Sub-Agent for Dinnerguest.
Your role is to discover and recommend delicious recipes based on mood, vibe, cravings, season, or weather.

Guidelines:
1. When a user asks for a recommendation based on mood, vibe, or vague cravings (e.g., "I want something cozy", "I need comfort food"):
   - Guide them with friendly, clarifying questions (up to 5 questions maximum).
   - For spicy cravings, ask if they prefer noodles or tacos.
   - For cozy/comfort cravings, ask if they want savory or sweet.
2. When the user specifies particular tastes, flavors, ingredients, or dish formats (e.g., "spicy tuna dish", "chicken soup", "sweet fruit crisp"):
   - Call `search_recipes_by_vibe` with their criteria to retrieve recipes from the vector database.
3. For every recommended recipe, present the recipe details clearly along with the allergen notice.
4. If unable to identify a specific recommendation within 5 turns, apologize politely and ask the user to provide more details.
"""


def search_recipes_by_vibe_adk(
    query: str,
    format: Optional[str] = None,
    flavor_profile: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Tool function for the Vibe Agent to search recipes by mood, vibe, or cravings."""
    tool = VibeSearchTool()
    filters = {}
    if format:
        filters["format"] = format
    if flavor_profile:
        filters["flavor_profile"] = flavor_profile
    results = tool.search_by_vibe(query, filters=filters, limit=limit)
    return [r.to_dict() for r in results]


vibe_agent = Agent(
    name="VibeAgent",
    model=os.getenv("GEN_MODEL", "gemini-2.5-flash"),
    description="Specialized sub-agent for discovering recipes by mood, vibe, season, cravings, and flavor profiles using vector RAG.",
    instruction=VIBE_AGENT_INSTRUCTION,
    tools=[search_recipes_by_vibe_adk],
)
