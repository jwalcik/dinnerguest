"""Strategic Model Router for Dinnerguest Agents.

Implements task- and result-aware model routing across Dinnerguest:
- `MenuAgent` uses `gemini-4.0-carbon` for complex multi-course meal planning,
  cross-dish allergen safety, and iterative menu modifications.
- `OnHandAgent` uses `gemini-3.8-flash` for direct ingredient matches and dynamically
  escalates to `gemini-4.0-carbon` when culinary substitutions or missing-ingredient
  adaptations are required.
- `VibeAgent` and `root_agent` default to `gemini-3.8-flash` for low-latency discovery
  and orchestration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dinnerguest.observability import get_structured_logger, log_agent_intent, start_span

logger = get_structured_logger(__name__)

FAST_MODEL = os.getenv("GEN_MODEL", "gemini-3.8-flash")
REASONING_MODEL = os.getenv("REASONING_MODEL", "gemini-4.0-carbon")


@dataclass
class RoutingDecision:
    """Represents a strategic model routing decision and its justification."""

    model: str
    tier: str  # "fast" | "reasoning"
    target_agent: str
    reason: str
    requires_substitution_reasoning: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "tier": self.tier,
            "target_agent": self.target_agent,
            "reason": self.reason,
            "requires_substitution_reasoning": self.requires_substitution_reasoning,
        }


class CulinaryModelRouter:
    """Routes agent turns and post-tool syntheses to the optimal Gemini model tier."""

    def __init__(
        self,
        fast_model: Optional[str] = None,
        reasoning_model: Optional[str] = None,
    ) -> None:
        self.fast_model = fast_model or os.getenv("GEN_MODEL", "gemini-3.8-flash")
        self.reasoning_model = reasoning_model or os.getenv("REASONING_MODEL", "gemini-4.0-carbon")

    def requires_substitution_reasoning(
        self,
        tool_result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Determines whether an OnHandAgent search result requires culinary substitution reasoning.

        Returns False when recipes are direct matches (`is_exact_match=True` with no
        substituted ingredients), and True when candidate recipes rely on ingredient
        substitutions or missing-ingredient adaptations.
        """
        if not isinstance(tool_result, dict):
            return False

        recipes = tool_result.get("recipes")
        if not isinstance(recipes, list) or not recipes:
            # No direct matches found — requires higher reasoning to suggest viable adaptations
            return True

        has_substitutions = any(bool(r.get("substituted_ingredients")) for r in recipes if isinstance(r, dict))
        if has_substitutions:
            return True

        # If every presented recipe is a direct exact match with zero missing items, use fast model
        all_direct_matches = all(
            bool(r.get("is_exact_match")) and not r.get("missing_ingredients")
            for r in recipes
            if isinstance(r, dict)
        )
        if all_direct_matches:
            return False

        # If top recipes have missing non-staple ingredients that could be substituted, use reasoning model
        has_missing_items = any(bool(r.get("missing_ingredients")) for r in recipes if isinstance(r, dict))
        return has_missing_items

    def route_on_hand_turn(
        self,
        user_msg: str = "",
        tool_result: Optional[Dict[str, Any]] = None,
    ) -> RoutingDecision:
        """Selects `gemini-3.8-flash` for direct on-hand matches and `gemini-4.0-carbon`
        when culinary substitutions or missing-ingredient adaptations may be required.
        """
        with start_span(
            "router.route_on_hand_turn",
            attributes={"has_tool_result": tool_result is not None},
        ):
            if tool_result is not None:
                needs_sub = self.requires_substitution_reasoning(tool_result)
                if needs_sub:
                    decision = RoutingDecision(
                        model=self.reasoning_model,
                        tier="reasoning",
                        target_agent="OnHandAgent",
                        reason=(
                            f"Escalating OnHandAgent to {self.reasoning_model}: candidate recipes "
                            "require culinary ingredient substitution or missing-item adaptation analysis."
                        ),
                        requires_substitution_reasoning=True,
                    )
                else:
                    decision = RoutingDecision(
                        model=self.fast_model,
                        tier="fast",
                        target_agent="OnHandAgent",
                        reason=(
                            f"Routing OnHandAgent to {self.fast_model}: direct ingredient matches "
                            "found without requiring complex substitutions."
                        ),
                        requires_substitution_reasoning=False,
                    )
                log_agent_intent(
                    action="model_router.on_hand_decision",
                    intent_description=decision.reason,
                    args=decision.to_dict(),
                )
                return decision

            return RoutingDecision(
                model=self.fast_model,
                tier="fast",
                target_agent="OnHandAgent",
                reason=f"Using {self.fast_model} for initial on-hand ingredient extraction and tool dispatch.",
                requires_substitution_reasoning=False,
            )

    def route_menu_planning_turn(self) -> RoutingDecision:
        """Routes MenuAgent turns to `gemini-4.0-carbon` for multi-course event planning."""
        decision = RoutingDecision(
            model=self.reasoning_model,
            tier="reasoning",
            target_agent="MenuAgent",
            reason=(
                f"Routing MenuAgent to {self.reasoning_model} for multi-course meal planning, "
                "serving scaling, and cross-course dietary/allergen reasoning."
            ),
            requires_substitution_reasoning=False,
        )
        log_agent_intent(
            action="model_router.menu_decision",
            intent_description=decision.reason,
            args=decision.to_dict(),
        )
        return decision

    def route_for_tool_call(
        self,
        tool_name: str,
        tool_result: Any,
        user_msg: str = "",
    ) -> RoutingDecision:
        """Determines the optimal model for synthesizing the response after a tool call executes."""
        if tool_name == "find_recipes_by_on_hand_ingredients":
            return self.route_on_hand_turn(
                user_msg=user_msg,
                tool_result=tool_result if isinstance(tool_result, dict) else None,
            )

        if tool_name in (
            "save_event_menu",
            "update_event_menu_dish",
            "get_event_menu",
            "list_event_menus",
            "generate_grocery_list_for_menu",
        ):
            return self.route_menu_planning_turn()

        return RoutingDecision(
            model=self.fast_model,
            tier="fast",
            target_agent="Dinnerguest",
            reason=f"Using {self.fast_model} for fast synthesis of '{tool_name}'.",
            requires_substitution_reasoning=False,
        )

    def adk_on_hand_model_routing_callback(
        self,
        callback_context: Any,
        llm_request: Any,
    ) -> Optional[Any]:
        """ADK `before_model_callback` for `OnHandAgent` that switches `llm_request.model`
        between `gemini-3.8-flash` (direct matches) and `gemini-4.0-carbon` (substitutions).
        """
        try:
            state = getattr(callback_context, "state", {}) if callback_context else {}
            last_on_hand_result = state.get("last_on_hand_result") if isinstance(state, dict) else None
            decision = self.route_on_hand_turn(tool_result=last_on_hand_result)
            if hasattr(llm_request, "model"):
                llm_request.model = decision.model
            if isinstance(state, dict):
                state["active_model"] = decision.model
                state["routing_decision"] = decision.to_dict()
        except Exception as exc:
            logger.debug("ADK OnHandAgent model routing callback skipped: %s", exc)
        return None


_DEFAULT_MODEL_ROUTER = CulinaryModelRouter()
