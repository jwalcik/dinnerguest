# Dinnerguest Agent Architecture & Guidelines (`AGENTS.md`)

Dinnerguest is an **agentic culinary assistant** built on the Google Agent Development Kit (ADK) and Vertex AI Gemini models. Every conversational turn, intent classification, clarifying question, sub-agent handoff, and tool selection must be driven by **agentic LLM reasoning**—not procedural `if/then` keyword lists or hardcoded regex routing trees.

---

## 1. Core Architectural Principle: Agentic Decision-Making vs. Procedural `if/then` Lists

### What We Do (Agentic Pattern)
- **Prompt-Driven Intent & Routing**: When improving how Dinnerguest interprets user requests, handles edge cases, or conducts multi-turn conversations, update the **system instructions (`instruction`)**, **agent descriptions (`description`)**, and **tool docstrings/schemas** in:
  - `src/dinnerguest/agent.py` (`DINNERGUEST_INSTRUCTION` and `root_agent`)
  - `src/dinnerguest/agents/menu_agent.py` (`MENU_AGENT_INSTRUCTION` and `menu_agent`)
  - `src/dinnerguest/agents/on_hand_agent.py` (`ON_HAND_AGENT_INSTRUCTION` and `on_hand_agent`)
  - `src/dinnerguest/agents/vibe_agent.py` (`VIBE_AGENT_INSTRUCTION` and `vibe_agent`)
- **Contextual Culinary Reasoning**: Allow the LLM agent to use semantic understanding and conversation history to:
  - Distinguish between **event/menu planning** (e.g., *"I'm having some friends over for game night, help me plan a menu"*, *"It's going to be 12 guests, not 6"*), **on-hand pantry discovery** (listing kitchen ingredients on hand), and **vibe/craving discovery** (mood, weather, or flavor cravings).
  - Ask natural clarifying questions when key planning parameters are missing (such as an unstated guest count, course preferences, or an event name/label).
  - Evaluate kitchen pantry staples and culinary substitutions in context (e.g., brown sugar for granulated sugar, shallots for onions, Greek yogurt in baking).

### What We Never Do (Anti-Pattern)
- **No Procedural `if/then` Keyword Lists for Intent Routing**: Never patch conversational routing issues by adding lists of verbs, nouns, or regex `if/elif/else` trees in `src/dinnerguest/server.py` to guess user intent or parse conversational state.
- **No Hardcoded Ingredient or Substitution Tables in Routing**: Never hardcode static lists of conversational filler words or sentence exclusions to decide whether a prompt is an ingredient list vs. a party planning request. Teach the `root_agent` and sub-agents via clear instructions and tool schemas.

---

## 2. Multi-Agent Hierarchy & Responsibilities

### `Dinnerguest` (`root_agent` — `src/dinnerguest/agent.py`)
The top-level orchestrator responsible for understanding the user's goal across multi-turn conversations and delegating to the appropriate sub-agent or tool:
- **Menu & Event Planning (`menu_agent`)**: Route any request to plan a meal, menu, party, holiday, game night, or gathering, as well as any follow-up turn that provides or updates the guest count, selects courses, replaces/adds/removes a dish, names/renames an event, recalls a saved menu, or requests an event grocery list.
- **On-Hand Ingredient Discovery (`on_hand_agent`)**: Route **only** when the user provides actual food ingredients they have on hand in their kitchen, fridge, or pantry and wants recipes they can make with those items.
- **Vibe & Craving Discovery (`vibe_agent`)**: Route when the user describes a mood, weather, flavor craving, or asks for a standalone single-dish recipe.
- **Direct Utility Tools**: Invoke `check_allergens_for_ingredients`, `scale_recipe_servings`, and `generate_grocery_list` for standalone allergen checks, single-recipe scaling, and ad-hoc shopping lists.

### `MenuAgent` (`menu_agent` — `src/dinnerguest/agents/menu_agent.py`)
Specialized sub-agent for multi-course event planning, persistent menu storage, and iterative menu refinement:
1. **Unstated Guest Count**: If the user has not specified a specific number of guests (e.g., *"Help me plan a menu for game night"* or *"I need to plan a menu for an upcoming party"*), **never assume a default guest count**. Ask the user how many guests they are expecting as part of the planning process.
2. **Course & Dish Selection**:
   - For major holidays with traditional menus (e.g., Thanksgiving, Easter), suggest appropriate traditional courses.
   - If a major holiday is not explicitly specified, ask the user about the number and types of dishes or courses to include in the plan.
3. **Arbitrary Event Labeling & Recall**: Allow the user to give the event any custom name/label (e.g., *"Saturday Gamenight"*, *"Game Night"*) so it can be saved via `save_event_menu` and recalled later via `get_event_menu` or `list_event_menus`. Support renaming via `update_event_menu_dish(action="rename", ...)`.
4. **Iterative Menu Updates**: Support swapping/replacing dishes (`action="replace"`), adding courses (`action="add"`), removing dishes (`action="remove"`), and updating the guest count (`action="scale"`, e.g., *"It's going to be 12 guests, not 6"*) while automatically rescaling servings and re-evaluating cross-course allergen and dietary safety.

### `OnHandAgent` (`on_hand_agent` — `src/dinnerguest/agents/on_hand_agent.py`)
Specialized sub-agent for matching on-hand pantry ingredients (`find_recipes_by_on_hand_ingredients`):
- Assumes universal kitchen staples (cooking oil, water, salt, pepper, basic baking essentials, common dry shelf spices) are available unless told otherwise.
- Applies culinary judgment for viable ingredient substitutions and explains why they work in the dish.
- Explicitly notes any unused user ingredients that can be saved for another meal, and caps recommendations at a maximum of 5 recipes per turn.

### `VibeAgent` (`vibe_agent` — `src/dinnerguest/agents/vibe_agent.py`)
Specialized sub-agent for mood, season, weather, and craving-based discovery (`search_recipes_by_vibe`):
- Asks up to 5 friendly clarifying questions when cravings are broad (e.g., narrowing spicy pork to tacos vs. noodles, or cozy food to savory vs. sweet) before executing vector/hybrid recipe search.

---

## 3. Scope of Deterministic Tools (`src/dinnerguest/tools/`)

Deterministic Python code is reserved strictly for **computational accuracy, safety compliance, and database retrieval** inside tools invoked by the agents:
- **Allergen Screening (`allergen_tool.py`)**: Deterministic FDA FALCPA Big 9 allergen detection and ambiguous ingredient flagging—agents must never guess or hallucinate allergen safety.
- **Recipe Scaling (`scaler_tool.py`)**: Exact proportional scaling, culinary fraction formatting (`1/2`, `1 1/2`), sub-`1/8 tsp` micro-measurement warnings, and bulk batch warnings.
- **Grocery Aggregation (`grocery_tool.py`)**: Unit normalization, duplicate ingredient summation across courses, and supermarket aisle grouping.
- **Persistence & Hybrid Search (`menu_store.py`, `pantry_search_tool.py`, `vibe_search_tool.py`, `database.py`)**: Firestore/JSON menu persistence and Cloud SQL `pgvector` + stem-overlap candidate retrieval.
