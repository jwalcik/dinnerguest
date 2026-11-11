"""On-Hand Ingredient Recommendations Sub-Agent.

Processes user inventory, computes recipe matches with culinary substitutions,
enforces 5-recipe pagination rules with match counts, and generates pantry staple inquiries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dinnerguest.tools.pantry_search_tool import (
    PantrySearchTool,
    RecipeMatch,
    SearchResult,
)


@dataclass
class OnHandAgentResponse:
    reply_text: str
    total_matches: int
    presented_count: int
    has_more: bool
    remaining_count: int
    recipes: List[RecipeMatch]
    staple_followups: List[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply_text": self.reply_text,
            "total_matches": self.total_matches,
            "presented_count": self.presented_count,
            "has_more": self.has_more,
            "remaining_count": self.remaining_count,
            "recipes": [r.to_dict() for r in self.recipes],
            "staple_followups": self.staple_followups,
        }


class OnHandRecommendationAgent:
    """Sub-agent responsible for ingredient-based recipe discovery and dialogue."""

    def __init__(self, search_tool: Optional[PantrySearchTool] = None):
        self.search_tool = search_tool or PantrySearchTool()

    def extract_ingredients_from_text(self, text: str) -> List[str]:
        """Simple extractor to pull candidate ingredients from conversational natural language."""
        cleaned = text.strip()
        # Remove trailing question clauses like "what can I make?", "what can I cook?", "any ideas?"
        cleaned = re.sub(
            r"[,;]?\s*(what\s+(can|should|could)\s+i\s+(make|cook|prepare).*|\bany\s+ideas\b.*|\bwhat\s+do\s+you\s+suggest\b.*)$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Clean common conversational filler prefixes
        cleaned = re.sub(
            r"^(i have|i've got|what can i make with|what can i cook with|looking for a recipe with)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(and|with|some|a couple of|a few|in my fridge|in my pantry)\b",
            ",",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Split on commas or semicolons
        tokens = [t.strip().rstrip(".").rstrip("?") for t in re.split(r"[,;]+", cleaned) if t.strip()]
        return tokens

    def process_request(
        self,
        user_input: str | List[str],
        page: int = 1,
        page_size: int = 5,
        max_missing: int = 2,
    ) -> OnHandAgentResponse:
        """Main entrypoint for processing an ingredient-based discovery inquiry."""
        if isinstance(user_input, str):
            ingredients = self.extract_ingredients_from_text(user_input)
        else:
            ingredients = user_input

        search_result = self.search_tool.search_recipes(
            on_hand_ingredients=ingredients,
            max_missing=max_missing,
            page=page,
            page_size=page_size,
        )

        reply_lines: List[str] = []

        if search_result.total_matches_count == 0:
            reply_lines.append(
                "I couldn't find any recipes that closely match what you currently have on hand. "
                "Try including a few pantry staples (like cooking oil, pasta, or flour) or tell me if you have any other vegetables or proteins!"
            )
            return OnHandAgentResponse(
                reply_text="\n".join(reply_lines),
                total_matches=0,
                presented_count=0,
                has_more=False,
                remaining_count=0,
                recipes=[],
                staple_followups=[],
            )

        reply_lines.append(f"Based on what you have on hand ({', '.join(ingredients)}), here are recipe options you can make:\n")

        for idx, recipe in enumerate(search_result.recipes, 1):
            status = "Complete Match" if recipe.is_exact_match else f"Missing {len(recipe.missing_ingredients)} item(s)"
            reply_lines.append(f"### {idx}. {recipe.title} (Serves {recipe.servings}) — *{status}*")

            # Direct matches
            if recipe.matched_ingredients:
                reply_lines.append(f"- **Matches**: {', '.join(recipe.matched_ingredients)}")

            # Substitutions
            if recipe.substituted_ingredients:
                for sub in recipe.substituted_ingredients:
                    reply_lines.append(f"- **Substitution**: Uses your **{sub.user_ingredient}** instead of {sub.recipe_ingredient} ({sub.note})")

            # Missing items
            if recipe.missing_ingredients:
                reply_lines.append(f"- **Missing**: {', '.join(recipe.missing_ingredients)}")

            # Allergen warning
            if recipe.allergen_report:
                reply_lines.append(f"- **Allergen Notice**: {recipe.allergen_report.get_guest_warning_summary()}")

            reply_lines.append("")

        # Pagination logic: rule enforcement
        if search_result.has_more:
            reply_lines.append(
                f"There are a total of **{search_result.total_matches_count} recipes** that match your request. "
                f"I've shown the top {len(search_result.recipes)}. Would you like to explore some of the other {search_result.remaining_count} options?"
            )
        elif search_result.total_matches_count <= 5 and search_result.total_matches_count > 1:
            reply_lines.append(f"I found a total of {search_result.total_matches_count} matching recipes.")

        # Pantry staple follow-up probing
        if search_result.staple_followup_prompts:
            reply_lines.append("")
            for prompt in search_result.staple_followup_prompts:
                reply_lines.append(f"💡 *Pantry Check:* {prompt}")

        return OnHandAgentResponse(
            reply_text="\n".join(reply_lines),
            total_matches=search_result.total_matches_count,
            presented_count=len(search_result.recipes),
            has_more=search_result.has_more,
            remaining_count=search_result.remaining_count,
            recipes=search_result.recipes,
            staple_followups=search_result.staple_followup_prompts,
        )
