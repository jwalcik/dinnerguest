"""On-Hand Ingredient Recommendations Sub-Agent.

Processes user inventory, computes recipe matches with culinary substitutions,
enforces 5-recipe pagination rules with match counts, and generates pantry staple inquiries.
"""

from __future__ import annotations

import os
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

    def extract_category_from_text(self, text: str) -> Optional[str]:
        """Detects if user requested a specific meal category or format (e.g. dessert, soup, dinner)."""
        lower = text.lower()
        if re.search(r"\b(dessert|sweet|baking|cookies|crisp|treat|treats)\b", lower):
            return "dessert"
        if re.search(r"\b(soup|stew|chowder)\b", lower):
            return "soup"
        if re.search(r"\b(breakfast|brunch|pancake|pancakes|waffle|waffles|eggs)\b", lower):
            return "breakfast"
        if re.search(r"\b(dinner|supper|main course|entree)\b", lower):
            return "dinner"
        return None

    def extract_ingredients_from_text(self, text: str) -> List[str]:
        """Extracts clean candidate ingredient tokens from conversational natural language."""
        cleaned = text.strip()

        # If there's an "I have" or "have" clause, isolate it
        m = re.search(r"\b(?:i have|i've got|we have|got)\s+(.*)", cleaned, re.IGNORECASE)
        if m:
            cleaned = m.group(1)

        # Always remove trailing question clauses like "is there a dessert I can make?", "what can I make?", "any ideas?"
        cleaned = re.sub(
            r"[,;]?\s*(is\s+there\s+(?:a\s+|any\s+)?[a-z\s]+(?:i|we)\s+can\s+(?:make|cook|bake|prepare).*|\bwhat\s+(?:can|should|could)\s+i\s+(?:make|cook|prepare).*|\bany\s+ideas\b.*|\bwhat\s+do\s+you\s+suggest\b.*|\bcan\s+i\s+make\s+.*)$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Clean common conversational filler prefixes
        cleaned = re.sub(
            r"^(?:i have|i've got|what can i make with|what can i cook with|looking for a recipe with)\s+",
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
        # Filter out stray conversational non-ingredient tokens
        non_ing_words = {"make", "cook", "prepare", "bake", "dessert", "dinner", "lunch", "breakfast", "recipe", "dish"}
        filtered_tokens = []
        for tok in tokens:
            tok_words = set(tok.lower().split())
            if tok_words.issubset(non_ing_words):
                continue
            filtered_tokens.append(tok)
        return filtered_tokens

    def process_request(
        self,
        user_input: str | List[str],
        category: Optional[str] = None,
        page: int = 1,
        page_size: int = 5,
        max_missing: int = 2,
    ) -> OnHandAgentResponse:
        """Main entrypoint for processing an ingredient-based discovery inquiry."""
        if isinstance(user_input, str):
            ingredients = self.extract_ingredients_from_text(user_input)
            if not category:
                category = self.extract_category_from_text(user_input)
        else:
            ingredients = user_input

        search_result = self.search_tool.search_recipes(
            on_hand_ingredients=ingredients,
            category=category,
            max_missing=max_missing,
            page=page,
            page_size=page_size,
        )

        # If no close matches under strict limit, check if missing common staples allows viable matches
        if search_result.total_matches_count == 0 and max_missing < 3:
            search_result = self.search_tool.search_recipes(
                on_hand_ingredients=ingredients,
                category=category,
                max_missing=3,
                page=page,
                page_size=page_size,
            )

        reply_lines: List[str] = []

        if search_result.total_matches_count == 0:
            category_mention = f" for {category}" if category else ""
            reply_lines.append(
                f"I couldn't find any recipes{category_mention} that closely match what you currently have on hand. "
                "Try including a few pantry staples (like cooking oil, pasta, or flour) or tell me if you have any other ingredients!"
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

        header_prefix = f"For a delicious {category}" if category else "Based on what you have on hand"
        reply_lines.append(f"{header_prefix} ({', '.join(ingredients)}), here are recipe options you can make:\n")

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

            # Unused items (clearly acknowledging ingredients not required for this recipe)
            if recipe.unused_ingredients:
                reply_lines.append(f"- **Unused Pantry Items**: Leaves your **{', '.join(recipe.unused_ingredients)}** for another meal")

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


# ADK Agent Definition
from dinnerguest.adk import Agent

ON_HAND_AGENT_INSTRUCTION = """You are the On-Hand Recipe Discovery Sub-Agent for Dinnerguest.
Your role is to find recipes matching ingredients the user already has in their pantry/refrigerator.

Guidelines:
1. Call `find_recipes_by_on_hand_ingredients` with the user's available ingredients and optional `category` (e.g., "dessert", "soup", "dinner").
2. A recipe does NOT need to use all of the user's ingredients. If a user has apples, brown sugar, oats, and an onion, recommend a dessert using the apples, sugar, and oats, and clearly note that the onion remains unused for another meal.
3. Consider and explain culinary substitutions (e.g., using brown sugar instead of granulated sugar, onion instead of shallots).
4. If a recipe is missing 1 or 2 pantry staples, ask polite follow-up questions (e.g., "Do you happen to have any dry pasta on hand?").
5. Never suggest more than 5 recipes at a time. If there are more than 5 matches, show the top 5, inform the user of the total N matching recipes, and ask if they'd like to see more.
6. For every recipe, clearly include the allergen notice.
"""


def find_recipes_by_on_hand_ingredients_adk(
    ingredients: List[str],
    category: Optional[str] = None,
    max_missing: int = 2,
    page: int = 1,
) -> Dict[str, Any]:
    tool = PantrySearchTool()
    return tool.search_recipes(
        on_hand_ingredients=ingredients,
        category=category,
        max_missing=max_missing,
        page=page,
        page_size=5,
    ).to_dict()


on_hand_agent = Agent(
    name="OnHandAgent",
    model=os.getenv("GEN_MODEL", "gemini-3.8-flash"),
    description="Specialized sub-agent for finding recipes matching pantry/on-hand ingredients with substitutions and staple probing.",
    instruction=ON_HAND_AGENT_INSTRUCTION,
    tools=[find_recipes_by_on_hand_ingredients_adk],
)

