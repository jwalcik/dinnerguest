"""Lightweight HTTP server entrypoint for Dinnerguest cloud container deployment.

Serves health check and chat invocation endpoints, an embedded Web UI, and wraps ADK agents.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dinnerguest — Culinary Planning Assistant</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #10b981;
      --primary-hover: #059669;
      --bubble-user: #0284c7;
      --bubble-bot: #334155;
      --border: #334155;
      --accent: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }
    header {
      background: var(--card-bg);
      border-bottom: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--primary);
    }
    .badge {
      background: #064e3b;
      color: #34d399;
      font-size: 0.75rem;
      padding: 4px 10px;
      border-radius: 9999px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .badge-dot {
      width: 8px;
      height: 8px;
      background: #34d399;
      border-radius: 50%;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .quick-prompts {
      padding: 12px 24px;
      background: rgba(30, 41, 59, 0.6);
      display: flex;
      gap: 8px;
      overflow-x: auto;
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }
    .prompt-btn {
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 0.85rem;
      cursor: pointer;
      white-space: nowrap;
      transition: all 0.2s;
    }
    .prompt-btn:hover {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
    }
    main {
      flex: 1;
      overflow-y: auto;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .message {
      max-width: 80%;
      padding: 14px 18px;
      border-radius: 14px;
      line-height: 1.55;
      font-size: 0.95rem;
      word-wrap: break-word;
      white-space: pre-wrap;
    }
    .message.bot {
      background: var(--bubble-bot);
      align-self: flex-start;
      border-bottom-left-radius: 4px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .message.user {
      background: var(--bubble-user);
      color: #fff;
      align-self: flex-end;
      border-bottom-right-radius: 4px;
    }
    footer {
      background: var(--card-bg);
      border-top: 1px solid var(--border);
      padding: 16px 24px;
      display: flex;
      gap: 12px;
      flex-shrink: 0;
    }
    input[type="text"] {
      flex: 1;
      background: #0f172a;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 12px 18px;
      border-radius: 8px;
      font-size: 1rem;
      outline: none;
      transition: border-color 0.2s;
    }
    input[type="text"]:focus {
      border-color: var(--primary);
    }
    button.send-btn {
      background: var(--primary);
      color: #fff;
      border: none;
      padding: 0 24px;
      border-radius: 8px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
    }
    button.send-btn:hover {
      background: var(--primary-hover);
    }
    button.send-btn:disabled {
      background: var(--border);
      cursor: not-allowed;
    }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>🍽️</span> Dinnerguest Agent
    </div>
    <div class="badge">
      <span class="badge-dot"></span> Cloud Run Ready
    </div>
  </header>

  <div class="quick-prompts">
    <button class="prompt-btn" onclick="sendPrompt('What can I cook with ground beef, onions, and canned tomatoes?')">🍲 Beef & Onions</button>
    <button class="prompt-btn" onclick="sendPrompt('I am craving a spicy pork dish')">🌶️ Spicy Pork Craving</button>
    <button class="prompt-btn" onclick="sendPrompt('What is something cozy I can cook for cold weather?')">🍂 Cozy Comfort Food</button>
    <button class="prompt-btn" onclick="sendPrompt('Check allergens for: pesto, milk, pasta, and peanut butter')">⚠️ Check Allergens</button>
    <button class="prompt-btn" onclick="sendPrompt('Plan a 3-course dinner party for 8 guests')">📋 Plan Dinner Party</button>
  </div>

  <main id="chat-box">
    <div class="message bot">
👋 <strong>Hello! I am Dinnerguest</strong>, your AI culinary orchestration assistant.

I can help you:
• <strong>Cook with what you have</strong>: Tell me ingredients in your fridge/pantry.
• <strong>Match your craving or mood</strong>: Ask for spicy noodles, cozy winter soups, or sweet treats.
• <strong>Scale recipes</strong>: Proportional scaling with small-batch and bulk-vessel safety checks.
• <strong>Screen allergens</strong>: Deterministic FDA Big 9 allergen and compound derivative checks.
• <strong>Plan party menus</strong>: Multi-course scaling, combined grocery shopping lists by aisle.

How can I assist your kitchen today?
    </div>
  </main>

  <footer>
    <input type="text" id="user-input" placeholder="Ask Dinnerguest about recipes, cravings, allergens, or party plans..." autocomplete="off">
    <button class="send-btn" id="send-btn" onclick="handleSubmit()">Send</button>
  </footer>

  <script>
    const sessionId = "session_" + Math.random().toString(36).substring(2, 10);
    const chatBox = document.getElementById("chat-box");
    const input = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSubmit();
      }
    });

    function appendMessage(text, sender) {
      const div = document.createElement("div");
      div.className = `message ${sender}`;
      div.innerHTML = text.replace(/\\n/g, "<br>");
      chatBox.appendChild(div);
      chatBox.scrollTop = chatBox.scrollHeight;
      return div;
    }

    function sendPrompt(text) {
      input.value = text;
      handleSubmit();
    }

    async function handleSubmit() {
      const text = input.value.trim();
      if (!text) return;

      appendMessage(text, "user");
      input.value = "";
      input.disabled = true;
      sendBtn.disabled = true;

      const loadingDiv = appendMessage("Thinking...", "bot");

      try {
        const resp = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, session_id: sessionId })
        });
        const data = await resp.json();
        loadingDiv.innerHTML = (data.reply || data.error || "No response received.").replace(/\\n/g, "<br>");
      } catch (err) {
        loadingDiv.textContent = "Error connecting to Dinnerguest agent: " + err.message;
      } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
        chatBox.scrollTop = chatBox.scrollHeight;
      }
    }
  </script>
</body>
</html>
"""

_SESSIONS: Dict[str, Dict[str, Any]] = {}

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel

    app = FastAPI(
        title="Dinnerguest Agent Engine Service",
        description="Multi-agent meal planning and culinary assistant service.",
        version="0.1.0",
    )

    class ChatRequest(BaseModel):
        message: str
        user_id: str = "default_user"
        session_id: str = "default_session"

    def get_service_metadata() -> Dict[str, Any]:
        try:
            from dinnerguest.agent import root_agent
            agent_name = getattr(root_agent, "name", "Dinnerguest")
            agent_desc = getattr(root_agent, "description", "Culinary orchestrator")
        except Exception as exc:
            logger.warning("Could not initialize root_agent: %s", exc)
            agent_name = "Dinnerguest"
            agent_desc = f"Culinary orchestrator (loading: {exc})"

        return {
            "service": "dinnerguest",
            "agent": agent_name,
            "description": agent_desc,
            "status": "ready",
            "endpoints": {
                "health": "/health",
                "ready": "/ready",
                "chat": "/chat",
                "ui": "/ui",
            },
        }

    @app.get("/health")
    @app.get("/ready")
    @app.get("/healthz")
    def health() -> Dict[str, str]:
        return {"status": "healthy", "service": "dinnerguest-agent"}

    @app.get("/ui", response_class=HTMLResponse)
    def ui() -> str:
        return HTML_PAGE

    @app.get("/")
    def root(request: Request) -> Any:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(content=HTML_PAGE)
        return JSONResponse(content=get_service_metadata())

    @app.post("/chat")
    def chat(payload: ChatRequest) -> Dict[str, Any]:
        user_msg = payload.message.strip()
        if not user_msg:
            return {"reply": "Please provide a culinary question, ingredient list, or craving!", "session_id": payload.session_id}

        session = _SESSIONS.setdefault(payload.session_id, {
            "vibe_state": None,
            "turn_count": 0,
        })
        session["turn_count"] += 1

        lower = user_msg.lower()

        try:
            # 1. In-progress Vibe Dialogue continuation
            if session.get("vibe_state") is not None and getattr(session["vibe_state"], "status", None) == "IN_PROGRESS":
                from dinnerguest.agents.vibe_agent import VibeRecommendationAgent
                vibe_agent = VibeRecommendationAgent()
                resp = vibe_agent.handle_message(user_msg, session["vibe_state"])
                if resp.state.status != "IN_PROGRESS":
                    session["vibe_state"] = None
                return {
                    "reply": resp.reply_text,
                    "session_id": payload.session_id,
                    "is_clarifying_question": resp.is_clarifying_question,
                    "data": resp.to_dict(),
                }

            # 2. Vibe / Mood Request Initiation
            vibe_keywords = ["craving", "crave", "vibe", "mood", "cozy", "cold weather", "spicy pork", "sweet dessert", "comfort food"]
            if any(k in lower for k in vibe_keywords) or (("want" in lower or "like" in lower) and ("spicy" in lower or "cozy" in lower or "sweet" in lower)):
                from dinnerguest.agents.vibe_agent import VibeDialogueState, VibeRecommendationAgent
                vibe_agent = VibeRecommendationAgent()
                state = VibeDialogueState(initial_query=user_msg)
                resp = vibe_agent.handle_message(user_msg, state)
                if resp.state.status == "IN_PROGRESS":
                    session["vibe_state"] = resp.state
                return {
                    "reply": resp.reply_text,
                    "session_id": payload.session_id,
                    "is_clarifying_question": resp.is_clarifying_question,
                    "data": resp.to_dict(),
                }

            # 3. Allergen Check Request
            if "allergen" in lower or "allergic" in lower or "falcpa" in lower or "safe for" in lower:
                from dinnerguest.tools.allergen_tool import AllergenChecker
                checker = AllergenChecker()
                # Extract potential ingredient list
                items = [i.strip() for i in re.split(r"[,;:\n]+", user_msg) if i.strip() and not any(w in i.lower() for w in ["check", "allergen", "safe", "for", "please"])]
                if not items:
                    items = [user_msg]
                report = checker.check_recipe_ingredients(items)
                summary = [f"🛡️ Allergen Safety Analysis for: {', '.join(items)}"]
                if report.detected_allergens:
                    summary.append(f"⚠️ Detected Allergens: {', '.join(report.detected_allergens)}")
                else:
                    summary.append("✅ No FDA FALCPA Big 9 allergens detected!")
                if report.ambiguous_ingredients:
                    summary.append(f"🔍 Items Requiring Label Verification: {', '.join(report.ambiguous_ingredients)}")
                return {
                    "reply": "\n".join(summary),
                    "session_id": payload.session_id,
                    "data": report.to_dict(),
                }

            # 4. Recipe Scaling Request
            if "scale" in lower or "servings" in lower or "double" in lower or "triple" in lower:
                from dinnerguest.tools.scaler_tool import RecipeScaler
                scaler = RecipeScaler()
                target = 8
                m = re.search(r"\b(?:to|for)\s+(\d+)\s*(?:servings|people|guests)?", lower)
                if m:
                    target = int(m.group(1))
                scale_res = scaler.scale_recipe(
                    recipe_title="Custom Recipe",
                    original_servings=4,
                    target_servings=target,
                    ingredients=["1 cup all-purpose flour", "1/2 cup granulated sugar", "1/4 tsp salt", "1/2 tsp vanilla extract", "1 egg"],
                )
                lines = [
                    f"⚖️ Scaled recipe yield from 4 to {target} servings (scale factor: {scale_res.scale_factor}):",
                    "",
                    "Ingredients:"
                ]
                for item in scale_res.scaled_ingredients:
                    lines.append(f"• {item.scaled_display}")
                if scale_res.warnings:
                    lines.append("")
                    for w in scale_res.warnings:
                        lines.append(f"⚠️ {w}")
                return {
                    "reply": "\n".join(lines),
                    "session_id": payload.session_id,
                    "data": scale_res.to_dict(),
                }

            # 5. On-Hand Ingredients Request
            pantry_clues = ["have", "fridge", "pantry", "make with", "cook with", "ingredients", "got"]
            if any(k in lower for k in pantry_clues) or "," in user_msg:
                from dinnerguest.agents.on_hand_agent import OnHandRecommendationAgent
                on_hand_agent = OnHandRecommendationAgent()
                resp = on_hand_agent.process_request(user_msg)
                return {
                    "reply": resp.reply_text,
                    "session_id": payload.session_id,
                    "data": resp.to_dict(),
                }

            # 6. Event Menu / Grocery List Request
            if "event" in lower or "party" in lower or "menu" in lower or "grocery" in lower or "shopping list" in lower:
                from dinnerguest.agent import _menu_agent
                menus = _menu_agent.list_saved_menus()
                if "grocery" in lower or "shopping" in lower:
                    if menus:
                        first_menu = menus[0]["name"]
                        glist = _menu_agent.generate_menu_grocery_list(first_menu)
                        if glist:
                            lines = [f"🛒 Consolidated Grocery List for '{first_menu}' ({glist.total_items} items):"]
                            for aisle, items in glist.by_aisle.items():
                                lines.append(f"\n[{aisle}]")
                                for itm in items:
                                    lines.append(f"• {itm.ingredient} ({itm.quantity} {itm.unit})")
                            return {"reply": "\n".join(lines), "session_id": payload.session_id}
                menu_names = [f"• {m['name']} ({m['guest_count']} guests)" for m in menus] if menus else ["(No saved event menus yet)"]
                reply = (
                    "🎉 Event Menu Planning:\n"
                    "I can organize multi-course dinner parties, automatically scale each dish to your guest count, and create an aisle-by-aisle shopping list.\n\n"
                    "Currently Saved Menus:\n" + "\n".join(menu_names)
                )
                return {"reply": reply, "session_id": payload.session_id}

            # 7. Fallback General Assistant Reply
            fallback = (
                "I'm here to help you in the kitchen! You can ask me:\n\n"
                "• 'What can I cook with ground beef, onions, and garlic?'\n"
                "• 'I am craving a spicy pork dish' or 'What is something cozy for dinner?'\n"
                "• 'Check allergens for milk, soy sauce, and shrimp'\n"
                "• 'Scale a recipe from 4 to 12 servings'\n"
                "• 'Plan a multi-course dinner party for 8 guests'"
            )
            return {"reply": fallback, "session_id": payload.session_id}

        except Exception as exc:
            logger.exception("Error processing chat message: %s", exc)
            return {
                "reply": f"I ran into an issue processing your request: {exc}. Please try again!",
                "error": str(exc),
                "session_id": payload.session_id,
            }

except ImportError:
    app = None  # type: ignore


def main() -> None:
    if app is None:
        raise RuntimeError(
            "FastAPI is not installed. Please install 'fastapi' and 'uvicorn' to run the server."
        )
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
