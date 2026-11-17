"""Lightweight HTTP server entrypoint for Dinnerguest cloud container deployment.

Serves health check and chat invocation endpoints, an interactive tracing Web UI,
Server-Sent Events streaming, and wraps ADK agents.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dinnerguest — Culinary Planning Assistant</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #131b2e;
      --panel-bg: #1c2640;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --primary: #10b981;
      --primary-hover: #059669;
      --bubble-user: #0284c7;
      --bubble-bot: #1e293b;
      --border: #263352;
      --accent: #f59e0b;
      --code-bg: #0b0f19;
      --thought-bg: #172033;
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
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--primary);
    }
    .header-badges {
      display: flex;
      align-items: center;
      gap: 12px;
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
      padding: 10px 24px;
      background: rgba(19, 27, 46, 0.7);
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
      gap: 20px;
    }
    .message-container {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-width: 85%;
    }
    .message-container.bot {
      align-self: flex-start;
    }
    .message-container.user {
      align-self: flex-end;
    }
    .message {
      padding: 16px 20px;
      border-radius: 14px;
      line-height: 1.6;
      font-size: 0.95rem;
      word-wrap: break-word;
    }
    .message.bot {
      background: var(--bubble-bot);
      border-bottom-left-radius: 4px;
      border: 1px solid var(--border);
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .message.user {
      background: var(--bubble-user);
      color: #fff;
      border-bottom-right-radius: 4px;
    }
    .trace-section {
      background: var(--thought-bg);
      border: 1px solid #2b3956;
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 8px;
      font-size: 0.85rem;
    }
    .trace-section summary {
      cursor: pointer;
      font-weight: 600;
      color: #38bdf8;
      display: flex;
      align-items: center;
      gap: 6px;
      user-select: none;
    }
    .trace-section summary:hover {
      color: #7dd3fc;
    }
    .thought-body {
      margin-top: 8px;
      color: var(--text-muted);
      line-height: 1.5;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.8rem;
      white-space: pre-wrap;
    }
    .tool-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 8px 0;
    }
    .tool-pill {
      background: #1e3a5f;
      border: 1px solid #2563eb;
      color: #60a5fa;
      font-size: 0.75rem;
      padding: 3px 8px;
      border-radius: 6px;
      cursor: pointer;
      font-family: ui-monospace, monospace;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: background 0.15s;
    }
    .tool-pill:hover {
      background: #2563eb;
      color: #fff;
    }
    .trace-footer {
      font-size: 0.75rem;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 4px;
    }
    .trace-footer a {
      color: #38bdf8;
      text-decoration: none;
    }
    .trace-footer a:hover {
      text-decoration: underline;
    }
    footer {
      background: var(--card-bg);
      border-top: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      gap: 12px;
      flex-shrink: 0;
    }
    input[type="text"] {
      flex: 1;
      background: var(--bg);
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
    /* Modal for Tool Payload Inspection */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.75);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 50;
      padding: 20px;
    }
    .modal-overlay.active {
      display: flex;
    }
    .modal-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      max-width: 700px;
      width: 100%;
      max-height: 80vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }
    .modal-header {
      padding: 14px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-title {
      font-weight: 700;
      font-size: 1rem;
      color: #38bdf8;
      font-family: ui-monospace, monospace;
    }
    .modal-close {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 1.5rem;
      cursor: pointer;
    }
    .modal-body {
      padding: 18px 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .code-block {
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.8rem;
      color: #e2e8f0;
      white-space: pre-wrap;
      overflow-x: auto;
    }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>🍽️</span> Dinnerguest Orchestrator
    </div>
    <div class="header-badges">
      <div class="badge">
        <span class="badge-dot"></span> Cloud Run & ADK Active
      </div>
    </div>
  </header>

  <div class="quick-prompts">
    <button class="prompt-btn" onclick="sendPrompt('What can I cook with ground beef, onions, and canned tomatoes?')">🍲 Beef & Onions</button>
    <button class="prompt-btn" onclick="sendPrompt('I am craving a spicy pork dish')">🌶️ Spicy Pork Craving</button>
    <button class="prompt-btn" onclick="sendPrompt('What is something cozy I can cook for cold weather?')">🍂 Cozy Comfort Food</button>
    <button class="prompt-btn" onclick="sendPrompt('Check allergens for: pesto, milk, pasta, and peanut butter')">⚠️ Check Allergens</button>
    <button class="prompt-btn" onclick="sendPrompt('Scale a recipe from 4 to 12 servings')">⚖️ Scale Recipe</button>
    <button class="prompt-btn" onclick="sendPrompt('Plan a 3-course dinner party for 8 guests')">📋 Plan Dinner Party</button>
  </div>

  <main id="chat-box">
    <div class="message-container bot">
      <div class="message bot">
👋 <strong>Hello! I am Dinnerguest</strong>, your AI culinary orchestration assistant.<br><br>
Ask me anything about cooking with pantry ingredients, mood-based recommendations, allergen screening, or multi-course party planning! Every turn shows agent reasoning and tool traces.
      </div>
    </div>
  </main>

  <footer>
    <input type="text" id="user-input" placeholder="Ask Dinnerguest about recipes, cravings, allergens, or party plans..." autocomplete="off">
    <button class="send-btn" id="send-btn" onclick="handleSubmit()">Send</button>
  </footer>

  <div class="modal-overlay" id="tool-modal" onclick="closeModal(event)">
    <div class="modal-card" onclick="event.stopPropagation()">
      <div class="modal-header">
        <div class="modal-title" id="modal-tool-name">Tool Execution Trace</div>
        <button class="modal-close" onclick="closeModal()">&times;</button>
      </div>
      <div class="modal-body">
        <strong>Input Arguments:</strong>
        <pre class="code-block" id="modal-tool-args"></pre>
        <strong>Tool Output Result:</strong>
        <pre class="code-block" id="modal-tool-result"></pre>
      </div>
    </div>
  </div>

  <script>
    const sessionId = "session_" + Math.random().toString(36).substring(2, 10);
    const chatBox = document.getElementById("chat-box");
    const input = document.getElementById("user-input");
    const sendBtn = document.getElementById("send-btn");
    const modal = document.getElementById("tool-modal");

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSubmit();
      }
    });

    function sendPrompt(text) {
      input.value = text;
      handleSubmit();
    }

    function closeModal() {
      modal.classList.remove("active");
    }

    function showToolModal(name, argsJson, resultJson) {
      document.getElementById("modal-tool-name").textContent = "⚡ Tool: " + name;
      document.getElementById("modal-tool-args").textContent = argsJson;
      document.getElementById("modal-tool-result").textContent = resultJson;
      modal.classList.add("active");
    }

    function appendUserMessage(text) {
      const container = document.createElement("div");
      container.className = "message-container user";
      const div = document.createElement("div");
      div.className = "message user";
      div.textContent = text;
      container.appendChild(div);
      chatBox.appendChild(container);
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    async function handleSubmit() {
      const text = input.value.trim();
      if (!text) return;

      appendUserMessage(text);
      input.value = "";
      input.disabled = true;
      sendBtn.disabled = true;

      // Create assistant container
      const botContainer = document.createElement("div");
      botContainer.className = "message-container bot";

      // Collapsible reasoning
      const traceDetails = document.createElement("details");
      traceDetails.className = "trace-section";
      traceDetails.open = true;
      const summary = document.createElement("summary");
      summary.innerHTML = "🧠 Agent Reasoning & Trace";
      const thoughtBody = document.createElement("div");
      thoughtBody.className = "thought-body";
      thoughtBody.textContent = "Analyzing query intent...";
      traceDetails.appendChild(summary);
      traceDetails.appendChild(thoughtBody);
      botContainer.appendChild(traceDetails);

      // Tool pills row
      const toolRow = document.createElement("div");
      toolRow.className = "tool-pills";
      botContainer.appendChild(toolRow);

      // Final message text container
      const msgDiv = document.createElement("div");
      msgDiv.className = "message bot";
      msgDiv.innerHTML = "<em>Cooking up a response...</em>";
      botContainer.appendChild(msgDiv);

      chatBox.appendChild(botContainer);
      chatBox.scrollTop = chatBox.scrollHeight;

      let traceId = null;
      let finalContent = "";
      let toolExecutions = [];

      try {
        const response = await fetch("/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, session_id: sessionId })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\\n");
          buffer = lines.pop(); // keep last incomplete line

          let currentEvent = null;

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.substring(7).trim();
            } else if (line.startsWith("data: ")) {
              const dataStr = line.substring(6).trim();
              if (!dataStr) continue;

              try {
                const data = JSON.parse(dataStr);

                if (currentEvent === "thought") {
                  thoughtBody.textContent = data.text || "";
                } else if (currentEvent === "tool_call") {
                  thoughtBody.textContent += "\\nCalling tool: " + data.tool + "...";
                } else if (currentEvent === "tool_result") {
                  thoughtBody.textContent += "\\nTool completed: " + data.tool;
                  const pill = document.createElement("button");
                  pill.className = "tool-pill";
                  pill.textContent = "⚡ " + data.tool;
                  const argsStr = JSON.stringify(data.args || {}, null, 2);
                  const resStr = JSON.stringify(data.result || {}, null, 2);
                  pill.onclick = () => showToolModal(data.tool, argsStr, resStr);
                  toolRow.appendChild(pill);
                } else if (currentEvent === "content") {
                  finalContent = data.text || "";
                  msgDiv.innerHTML = finalContent.replace(/\\n/g, "<br>");
                } else if (currentEvent === "done") {
                  traceId = data.trace_id;
                  if (data.reply) {
                    finalContent = data.reply;
                    msgDiv.innerHTML = finalContent.replace(/\\n/g, "<br>");
                  }
                }
              } catch (e) {
                console.error("Error parsing event:", e, line);
              }
            }
          }
        }

        // Add trace footer
        if (traceId) {
          const footer = document.createElement("div");
          footer.className = "trace-footer";
          footer.innerHTML = `Trace ID: <code>${traceId}</code> • <a href="https://console.cloud.google.com/traces/traces?tid=${traceId}" target="_blank">View Cloud Trace Waterfall ↗</a>`;
          botContainer.appendChild(footer);
        }

      } catch (err) {
        msgDiv.textContent = "Error connecting to Dinnerguest service: " + err.message;
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


def _extract_trace_id(request: Any = None) -> str:
    if request and hasattr(request, "headers"):
        cloud_trace = request.headers.get("x-cloud-trace-context")
        if cloud_trace:
            return cloud_trace.split("/")[0].split(";")[0]
    return f"{int(time.time()*1000):x}{os.getpid():04x}"


def _try_gemini_agent_turn(user_msg: str, session_id: str, trace_id: str) -> Optional[Dict[str, Any]]:
    """Attempts to execute the agent turn using Gemini with tools via Google GenAI / Vertex AI.

    Falls back cleanly to the deterministic agent pipeline if GenAI is unavailable or fails.
    """
    project = os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project or os.getenv("DISABLE_LLM_AGENT", "false").lower() == "true":
        return None

    try:
        from google import genai
        from google.genai import types
        from dinnerguest.agent import (
            find_recipes_by_on_hand_ingredients,
            search_recipes_by_vibe,
            check_allergens_for_ingredients,
            scale_recipe_servings,
            generate_grocery_list,
            save_event_menu,
            get_event_menu,
            list_event_menus,
            generate_grocery_list_for_menu,
            DINNERGUEST_INSTRUCTION,
        )

        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        model_name = os.getenv("GEN_MODEL", "gemini-2.5-flash")

        client = genai.Client(vertexai=True, project=project, location=location)
        available_tools = [
            find_recipes_by_on_hand_ingredients,
            search_recipes_by_vibe,
            check_allergens_for_ingredients,
            scale_recipe_servings,
            generate_grocery_list,
            save_event_menu,
            get_event_menu,
            list_event_menus,
            generate_grocery_list_for_menu,
        ]

        response = client.models.generate_content(
            model=model_name,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=DINNERGUEST_INSTRUCTION,
                tools=available_tools,
                temperature=0.2,
            ),
        )

        events: List[Dict[str, Any]] = [
            {"type": "thought", "text": f"Dinnerguest LLM reasoning active ({model_name}). Processing query with tool orchestrator..."}
        ]

        if getattr(response, "function_calls", None):
            for call in response.function_calls:
                events.append({
                    "type": "tool_call",
                    "tool": getattr(call, "name", "tool"),
                    "args": dict(getattr(call, "args", {}) or {}),
                })

        reply_text = getattr(response, "text", "") or ""
        if reply_text.strip():
            return {
                "reply": reply_text.strip(),
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
            }
    except Exception as exc:
        logger.info("Vertex AI LLM agent pass skipped (%s); executing deterministic culinary pipeline", exc)
        return None

    return None


def _execute_agent_turn(user_msg: str, session_id: str, trace_id: str) -> Dict[str, Any]:
    # 0. Attempt LLM Agent execution via Google GenAI / Vertex AI if active
    llm_result = _try_gemini_agent_turn(user_msg, session_id, trace_id)
    if llm_result is not None:
        return llm_result

    # 1. Deterministic Agent Pipeline Fallback
    return _execute_deterministic_agent_turn(user_msg, session_id, trace_id)


def _execute_deterministic_agent_turn(user_msg: str, session_id: str, trace_id: str) -> Dict[str, Any]:
    session = _SESSIONS.setdefault(session_id, {
        "vibe_state": None,
        "turn_count": 0,
    })
    session["turn_count"] += 1
    lower = user_msg.lower()

    events: List[Dict[str, Any]] = []

    # 1. In-progress Vibe Dialogue continuation
    if session.get("vibe_state") is not None and getattr(session["vibe_state"], "status", None) == "IN_PROGRESS":
        events.append({"type": "thought", "text": "Continuing conversational vibe discovery dialogue..."})
        from dinnerguest.agents.vibe_agent import VibeRecommendationAgent
        vibe_agent = VibeRecommendationAgent()
        events.append({"type": "tool_call", "tool": "vibe_agent_handle_message", "args": {"user_message": user_msg}})
        resp = vibe_agent.handle_message(user_msg, session["vibe_state"])
        events.append({"type": "tool_result", "tool": "vibe_agent_handle_message", "args": {"user_message": user_msg}, "result": resp.to_dict()})
        if resp.state.status != "IN_PROGRESS":
            session["vibe_state"] = None
        return {
            "reply": resp.reply_text,
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
            "data": resp.to_dict(),
        }

    # 2. Vibe / Mood Request Initiation
    vibe_keywords = ["craving", "crave", "vibe", "mood", "cozy", "cold weather", "spicy pork", "sweet dessert", "comfort food"]
    if any(k in lower for k in vibe_keywords) or (("want" in lower or "like" in lower) and ("spicy" in lower or "cozy" in lower or "sweet" in lower)):
        events.append({"type": "thought", "text": "Detected craving or mood-based request. Initiating conversational vibe discovery..."})
        from dinnerguest.agents.vibe_agent import VibeDialogueState, VibeRecommendationAgent
        vibe_agent = VibeRecommendationAgent()
        state = VibeDialogueState(initial_query=user_msg)
        events.append({"type": "tool_call", "tool": "search_recipes_by_vibe", "args": {"query": user_msg}})
        resp = vibe_agent.handle_message(user_msg, state)
        events.append({"type": "tool_result", "tool": "search_recipes_by_vibe", "args": {"query": user_msg}, "result": resp.to_dict()})
        if resp.state.status == "IN_PROGRESS":
            session["vibe_state"] = resp.state
        return {
            "reply": resp.reply_text,
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
            "data": resp.to_dict(),
        }

    # 3. Allergen Check Request
    if "allergen" in lower or "allergic" in lower or "falcpa" in lower or "safe for" in lower:
        events.append({"type": "thought", "text": "Detected allergen inquiry. Running deterministic FDA FALCPA Big 9 screening..."})
        from dinnerguest.tools.allergen_tool import AllergenChecker
        checker = AllergenChecker()
        items = [i.strip() for i in re.split(r"[,;:\n]+", user_msg) if i.strip() and not any(w in i.lower() for w in ["check", "allergen", "safe", "for", "please"])]
        if not items:
            items = [user_msg]
        events.append({"type": "tool_call", "tool": "check_allergens_for_ingredients", "args": {"ingredients": items}})
        report = checker.check_recipe_ingredients(items)
        events.append({"type": "tool_result", "tool": "check_allergens_for_ingredients", "args": {"ingredients": items}, "result": report.to_dict()})

        summary = [f"🛡️ Allergen Safety Analysis for: {', '.join(items)}"]
        if report.detected_allergens:
            summary.append(f"⚠️ Detected Allergens: {', '.join(report.detected_allergens)}")
        else:
            summary.append("✅ No FDA FALCPA Big 9 allergens detected!")
        if report.ambiguous_ingredients:
            summary.append(f"🔍 Items Requiring Label Verification: {', '.join(report.ambiguous_ingredients)}")

        return {
            "reply": "\n".join(summary),
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
            "data": report.to_dict(),
        }

    # 4. Recipe Scaling Request
    if "scale" in lower or "servings" in lower or "double" in lower or "triple" in lower:
        events.append({"type": "thought", "text": "Detected recipe scaling request. Applying proportional scaling and bulk/min safety checks..."})
        from dinnerguest.tools.scaler_tool import RecipeScaler
        scaler = RecipeScaler()
        target = 8
        m = re.search(r"\b(?:to|for)\s+(\d+)\s*(?:servings|people|guests)?", lower)
        if m:
            target = int(m.group(1))
        ingredients = ["1 cup all-purpose flour", "1/2 cup granulated sugar", "1/4 tsp salt", "1/2 tsp vanilla extract", "1 egg"]
        events.append({"type": "tool_call", "tool": "scale_recipe_servings", "args": {"recipe_title": "Custom Recipe", "original_servings": 4, "target_servings": target}})
        scale_res = scaler.scale_recipe(
            recipe_title="Custom Recipe",
            original_servings=4,
            target_servings=target,
            ingredients=ingredients,
        )
        events.append({"type": "tool_result", "tool": "scale_recipe_servings", "args": {"target_servings": target}, "result": scale_res.to_dict()})

        lines = [
            f"⚖️ Scaled recipe yield from 4 to {target} servings (scale factor: {scale_res.scale_factor}):",
            "",
            "Ingredients:"
        ]
        for item in scale_res.scaled_ingredients:
            lines.append(f"• {item.scaled_text}")
        if scale_res.warnings:
            lines.append("")
            for w in scale_res.warnings:
                lines.append(f"⚠️ {w}")
        return {
            "reply": "\n".join(lines),
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
            "data": scale_res.to_dict(),
        }

    # 5. On-Hand Ingredients Request
    pantry_clues = ["have", "fridge", "pantry", "make with", "cook with", "ingredients", "got"]
    if any(k in lower for k in pantry_clues) or "," in user_msg:
        events.append({"type": "thought", "text": "Analyzing user pantry ingredients, matching culinary substitutions, and checking missing staples..."})
        from dinnerguest.agents.on_hand_agent import OnHandRecommendationAgent
        on_hand_agent = OnHandRecommendationAgent()
        extracted = on_hand_agent.extract_ingredients_from_text(user_msg)
        events.append({"type": "tool_call", "tool": "find_recipes_by_on_hand_ingredients", "args": {"ingredients": extracted}})
        resp = on_hand_agent.process_request(user_msg)
        events.append({"type": "tool_result", "tool": "find_recipes_by_on_hand_ingredients", "args": {"ingredients": extracted}, "result": resp.to_dict()})
        return {
            "reply": resp.reply_text,
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
            "data": resp.to_dict(),
        }

    # 6. Event Menu / Grocery List Request
    if "event" in lower or "party" in lower or "menu" in lower or "grocery" in lower or "shopping list" in lower:
        events.append({"type": "thought", "text": "Accessing persistent event menu store and grocery aggregator..."})
        from dinnerguest.agent import _menu_agent
        events.append({"type": "tool_call", "tool": "list_event_menus", "args": {}})
        menus = _menu_agent.list_saved_menus()
        events.append({"type": "tool_result", "tool": "list_event_menus", "args": {}, "result": menus})

        if "grocery" in lower or "shopping" in lower:
            if menus:
                first_menu = menus[0]["name"]
                events.append({"type": "tool_call", "tool": "generate_grocery_list_for_menu", "args": {"name_or_id": first_menu}})
                glist = _menu_agent.generate_menu_grocery_list(first_menu)
                events.append({"type": "tool_result", "tool": "generate_grocery_list_for_menu", "args": {"name_or_id": first_menu}, "result": glist.to_dict() if glist else {}})
                if glist:
                    lines = [f"🛒 Consolidated Grocery List for '{first_menu}' ({glist.total_items} items):"]
                    for aisle, items in glist.by_aisle.items():
                        lines.append(f"\n[{aisle}]")
                        for itm in items:
                            lines.append(f"• {itm.ingredient} ({itm.quantity} {itm.unit})")
                    return {
                        "reply": "\n".join(lines),
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "events": events,
                    }

        menu_names = [f"• {m['name']} ({m['guest_count']} guests)" for m in menus] if menus else ["(No saved event menus yet)"]
        reply = (
            "🎉 Event Menu Planning:\n"
            "I can organize multi-course dinner parties, automatically scale each dish to your guest count, and create an aisle-by-aisle shopping list.\n\n"
            "Currently Saved Menus:\n" + "\n".join(menu_names)
        )
        return {
            "reply": reply,
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
        }

    # 7. Direct Recipe Search / Instruction Request
    recipe_clues = [
        "recipe", "soup", "stew", "skillet", "fajita", "fajitas", "cookie", "cookies",
        "crisp", "pesto", "bolognese", "taco", "tacos", "meatball", "meatballs",
        "burger", "burgers", "chicken", "beef", "pork", "pasta", "noodles",
        "how to cook", "how to make", "how do i make", "how do i cook", "how do you make",
        "dinner", "lunch", "dessert", "cook", "prepare", "make",
    ]
    if any(k in lower for k in recipe_clues):
        events.append({"type": "thought", "text": "Searching culinary database and recipe catalog for matching instructions and allergen profile..."})
        from dinnerguest.tools.vibe_search_tool import VibeSearchTool
        vibe_tool = VibeSearchTool()
        events.append({"type": "tool_call", "tool": "search_recipes_by_vibe", "args": {"query": user_msg}})
        results = vibe_tool.search_by_vibe(user_msg, limit=3)
        events.append({"type": "tool_result", "tool": "search_recipes_by_vibe", "args": {"query": user_msg}, "result": [r.to_dict() for r in results]})

        if results:
            best = results[0]
            lines = [
                f"🍲 Here is a great recipe for **{best.title}**:",
                f"- **Servings**: {best.servings}",
                f"- **Time**: {best.prep_time_minutes} min prep, {best.cook_time_minutes} min cook",
                "",
                "### Ingredients:"
            ]
            for ing in best.ingredients:
                lines.append(f"• {ing}")
            lines.append("")
            lines.append("### Instructions:")
            for idx, step in enumerate(best.instructions, 1):
                lines.append(f"{idx}. {step}")
            if best.allergen_report:
                lines.append("")
                lines.append(f"⚠️ **Allergen Notice**: {best.allergen_report.get_guest_warning_summary()}")
            if len(results) > 1:
                other_titles = [f"'{r.title}'" for r in results[1:]]
                lines.append("")
                lines.append(f"💡 *Other matching options: {', '.join(other_titles)}.*")

            return {
                "reply": "\n".join(lines),
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
                "data": [r.to_dict() for r in results],
            }

    # 8. Fallback General Assistant Reply
    events.append({"type": "thought", "text": "Providing culinary guidance overview and suggestion options."})
    fallback = (
        "I'm here to help you in the kitchen! You can ask me:\n\n"
        "• 'What can I cook with ground beef, onions, and garlic?'\n"
        "• 'I am craving a spicy pork dish' or 'What is something cozy for dinner?'\n"
        "• 'Check allergens for milk, soy sauce, and shrimp'\n"
        "• 'Scale a recipe from 4 to 12 servings'\n"
        "• 'Plan a multi-course dinner party for 8 guests'"
    )
    return {
        "reply": fallback,
        "session_id": session_id,
        "trace_id": trace_id,
        "events": events,
    }


try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
                "chat_stream": "/chat/stream",
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
    def chat(payload: ChatRequest, request: Request) -> Dict[str, Any]:
        trace_id = _extract_trace_id(request)
        user_msg = payload.message.strip()
        if not user_msg:
            return {"reply": "Please provide a culinary question, ingredient list, or craving!", "session_id": payload.session_id, "trace_id": trace_id}
        try:
            return _execute_agent_turn(user_msg, payload.session_id, trace_id)
        except Exception as exc:
            logger.exception("Error in /chat: %s", exc)
            return {"reply": f"Error: {exc}", "error": str(exc), "session_id": payload.session_id, "trace_id": trace_id}

    @app.post("/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        trace_id = _extract_trace_id(request)
        user_msg = payload.message.strip()

        async def sse_generator() -> AsyncGenerator[str, None]:
            if not user_msg:
                yield f"event: done\ndata: {json.dumps({'reply': 'Please provide a culinary question!', 'trace_id': trace_id})}\n\n"
                return

            try:
                # 1. Emit Initial Thought
                yield f"event: thought\ndata: {json.dumps({'text': 'Processing inquiry and determining tool routing...'})}\n\n"
                await asyncio.sleep(0.05)

                # Run turn logic
                result = _execute_agent_turn(user_msg, payload.session_id, trace_id)

                for ev in result.get("events", []):
                    ev_type = ev["type"]
                    if ev_type == "thought":
                        yield f"event: thought\ndata: {json.dumps({'text': ev['text']})}\n\n"
                    elif ev_type == "tool_call":
                        yield f"event: tool_call\ndata: {json.dumps({'tool': ev['tool'], 'args': ev['args']})}\n\n"
                    elif ev_type == "tool_result":
                        yield f"event: tool_result\ndata: {json.dumps({'tool': ev['tool'], 'args': ev.get('args', {}), 'result': ev['result']})}\n\n"
                    await asyncio.sleep(0.03)

                # Emit final content
                yield f"event: content\ndata: {json.dumps({'text': result['reply']})}\n\n"
                yield f"event: done\ndata: {json.dumps({'reply': result['reply'], 'trace_id': trace_id, 'session_id': payload.session_id})}\n\n"

            except Exception as exc:
                logger.exception("Error in /chat/stream: %s", exc)
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

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
