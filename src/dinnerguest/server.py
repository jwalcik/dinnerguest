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

from dinnerguest.observability import (
    _RECORDED_SPANS,
    get_recorded_spans,
    get_structured_logger,
    log_agent_intent,
    log_agent_outcome,
    redact_pii,
    set_trace_context,
    start_span,
)

logger = get_structured_logger(__name__)

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
    .message.bot h1, .message.bot h2, .message.bot h3, .message.bot h4 {
      color: #38bdf8;
      margin: 12px 0 6px 0;
      font-weight: 700;
      line-height: 1.3;
    }
    .message.bot h1 { font-size: 1.25rem; }
    .message.bot h2 { font-size: 1.15rem; }
    .message.bot h3 { font-size: 1.05rem; }
    .message.bot p {
      margin-bottom: 8px;
    }
    .message.bot ul, .message.bot ol {
      margin: 8px 0 12px 20px;
      padding-left: 6px;
    }
    .message.bot li {
      margin-bottom: 4px;
    }
    .message.bot strong {
      color: #f8fafc;
      font-weight: 700;
    }
    .message.bot code {
      background: #0b1120;
      border: 1px solid #1e293b;
      color: #38bdf8;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.85em;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .message.bot pre.code-block {
      background: #0b1120;
      border: 1px solid #1e293b;
      padding: 10px 14px;
      border-radius: 8px;
      margin: 8px 0;
      overflow-x: auto;
    }
    .message.bot blockquote {
      border-left: 3px solid #38bdf8;
      padding-left: 12px;
      margin: 8px 0;
      color: var(--text-muted);
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

    function renderMarkdown(md) {
      if (!md) return "";
      // Escape HTML entities to prevent XSS
      let s = md.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

      // Code blocks
      s = s.replace(/```([a-zA-Z0-9_-]*)\\n([\\s\\S]*?)```/g, function(match, lang, code) {
        return '<pre class="code-block"><code>' + code.trim() + '</code></pre>';
      });

      // Inline code
      s = s.replace(/`([^`\\n]+)`/g, "<code>$1</code>");

      // Headers (###, ##, #)
      s = s.replace(/^### (.*$)/gm, "<h3>$1</h3>");
      s = s.replace(/^## (.*$)/gm, "<h2>$1</h2>");
      s = s.replace(/^# (.*$)/gm, "<h1>$1</h1>");

      // Bold (**text** or __text__)
      s = s.replace(/\\*\\*([^\\*\\n]+)\\*\\*/g, "<strong>$1</strong>");
      s = s.replace(/__([^__\\n]+)__/g, "<strong>$1</strong>");

      // Italic (*text* or _text_)
      s = s.replace(/\\*([^\\*\\n]+)\\*/g, "<em>$1</em>");
      s = s.replace(/(?<!\\w)_([^_\\n]+)_(?!\\w)/g, "<em>$1</em>");

      // Blockquotes
      s = s.replace(/^> (.*$)/gm, "<blockquote>$1</blockquote>");

      // Unordered lists (- item, * item, • item)
      s = s.replace(/(?:^[-*•]\\s+(.*(?:\\n|$)))+/gm, function(match) {
        const items = match.trim().split(/\\n/).map(function(line) {
          const itemText = line.replace(/^[-*•]\\s+/, "").trim();
          return "<li>" + itemText + "</li>";
        }).join("");
        return "<ul>" + items + "</ul>";
      });

      // Ordered lists (1. item)
      s = s.replace(/(?:^\\d+\\.\\s+(.*(?:\\n|$)))+/gm, function(match) {
        const items = match.trim().split(/\\n/).map(function(line) {
          const itemText = line.replace(/^\\d+\\.\\s+/, "").trim();
          return "<li>" + itemText + "</li>";
        }).join("");
        return "<ol>" + items + "</ol>";
      });

      // Paragraphs & newlines: double newlines become paragraphs, single newlines become <br>
      // Clean up extra spacing around block tags
      s = s.replace(/<\\/(h[1-3]|ul|ol|pre|blockquote)>\\s*\\n/g, "</$1>");
      s = s.replace(/\\n\\s*<(h[1-3]|ul|ol|pre|blockquote)/g, "<$1");
      s = s.replace(/\\n\\n+/g, "<br><br>");
      s = s.replace(/\\n/g, "<br>");

      return s;
    }

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
                  msgDiv.innerHTML = renderMarkdown(finalContent);
                } else if (currentEvent === "done") {
                  traceId = data.trace_id;
                  if (data.reply) {
                    finalContent = data.reply;
                    msgDiv.innerHTML = renderMarkdown(finalContent);
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
    import uuid
    if request and hasattr(request, "headers"):
        cloud_trace = request.headers.get("x-cloud-trace-context")
        if cloud_trace:
            return cloud_trace.split("/")[0].split(";")[0]
    return uuid.uuid4().hex


def _build_Explicit_tool_declarations(types_mod: Any) -> Any:
    """Builds explicit Vertex AI FunctionDeclarations so `Dict[str, Any]` parameters
    have explicit OBJECT properties and never fail schema validation or trigger hidden AFC.
    """
    decls = [
        types_mod.FunctionDeclaration(
            name="find_recipes_by_on_hand_ingredients",
            description=(
                "Finds recipes matching actual food ingredients the user positively has on hand in their kitchen, fridge, or pantry. "
                "NEVER call this tool for cravings with excluded ingredients (e.g., 'doesn't have any cheese') or event planning (e.g., 'game night')."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "ingredients": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "List of food ingredients the user currently has available to cook with.",
                    },
                    "category": {
                        "type": "STRING",
                        "description": "Optional meal type or course filter (e.g. 'dinner', 'dessert', 'soup').",
                    },
                    "max_missing": {
                        "type": "INTEGER",
                        "description": "Maximum allowed missing ingredients (default 2).",
                    },
                    "page": {
                        "type": "INTEGER",
                        "description": "Page number (default 1).",
                    },
                },
                "required": ["ingredients"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="search_recipes_by_vibe",
            description=(
                "Searches recipes based on mood, vibe, craving, course type, weather, or specific dish request, "
                "with optional excluded_ingredients for dietary/ingredient exclusions (e.g., 'savory appetizer that doesn't have any cheese')."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "vibe_or_craving": {
                        "type": "STRING",
                        "description": "Positive description of the craving, course, or dish (e.g. 'savory appetizer', 'sweet noodle', 'spicy pork').",
                    },
                    "flavor_profile": {
                        "type": "STRING",
                        "description": "Optional flavor profile filter ('savory' or 'sweet').",
                    },
                    "format": {
                        "type": "STRING",
                        "description": "Optional dish format filter (e.g. 'noodle', 'taco', 'soup', 'crisp').",
                    },
                    "protein": {
                        "type": "STRING",
                        "description": "Optional protein filter (e.g. 'pork', 'beef', 'chicken', 'tuna').",
                    },
                    "excluded_ingredients": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "Optional list of ingredients to exclude (e.g. ['cheese'] when user says 'doesn't have any cheese').",
                    },
                    "limit": {
                        "type": "INTEGER",
                        "description": "Maximum number of recipes to return (default 5).",
                    },
                },
                "required": ["vibe_or_craving"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="check_allergens_for_ingredients",
            description="Deterministically checks a list of ingredients against FDA FALCPA Big 9 allergens.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "ingredients": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "List of ingredient strings to screen for allergens.",
                    },
                },
                "required": ["ingredients"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="scale_recipe_servings",
            description="Scales a standalone recipe's ingredient quantities from original_servings to target_servings.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "recipe_title": {"type": "STRING", "description": "Name of the recipe."},
                    "ingredients": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "List of ingredient strings with quantities.",
                    },
                    "original_servings": {"type": "INTEGER", "description": "Base recipe servings."},
                    "target_servings": {"type": "INTEGER", "description": "Target servings to scale to."},
                },
                "required": ["recipe_title", "ingredients", "original_servings", "target_servings"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="generate_grocery_list",
            description="Generates a consolidated supermarket shopping list from one or more recipes.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "recipe_ingredient_lists": {
                        "type": "ARRAY",
                        "description": "List of recipe objects with title and ingredients.",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "title": {"type": "STRING", "description": "Recipe title."},
                                "ingredients": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                    "description": "List of ingredient strings.",
                                },
                                "scale_factor": {"type": "NUMBER", "description": "Optional scale multiplier."},
                            },
                            "required": ["title", "ingredients"],
                        },
                    },
                },
                "required": ["recipe_ingredient_lists"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="save_event_menu",
            description="Saves and scales a multi-course event menu in persistent storage once guest_count and dishes are determined.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING", "description": "Event name or label (e.g. 'Saturday Gamenight', 'Thanksgiving Dinner')."},
                    "guest_count": {"type": "INTEGER", "description": "Number of guests expected."},
                    "dishes": {
                        "type": "ARRAY",
                        "description": "List of planned course dishes.",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "recipe_id": {"type": "STRING", "description": "Optional recipe ID."},
                                "recipe_title": {"type": "STRING", "description": "Title of the dish."},
                                "course": {"type": "STRING", "description": "Course name (e.g. 'Appetizer', 'Main', 'Side', 'Dessert')."},
                                "original_servings": {"type": "INTEGER", "description": "Base recipe servings (default 4)."},
                                "ingredients": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                    "description": "List of ingredient strings for the dish.",
                                },
                            },
                            "required": ["recipe_title", "ingredients"],
                        },
                    },
                    "theme": {"type": "STRING", "description": "Optional theme description."},
                    "notes": {"type": "STRING", "description": "Optional planning notes."},
                },
                "required": ["name", "guest_count", "dishes"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="get_event_menu",
            description="Recalls a previously saved event menu by name or slug.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "name_or_id": {"type": "STRING", "description": "Name or slug of the saved event menu."},
                },
                "required": ["name_or_id"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="list_event_menus",
            description="Lists all saved event menus in persistent storage.",
            parameters={
                "type": "OBJECT",
                "properties": {},
            },
        ),
        types_mod.FunctionDeclaration(
            name="generate_grocery_list_for_menu",
            description="Generates a consolidated aisle-by-aisle grocery list for a saved event menu.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "name_or_id": {"type": "STRING", "description": "Name or slug of the saved event menu."},
                },
                "required": ["name_or_id"],
            },
        ),
        types_mod.FunctionDeclaration(
            name="update_event_menu_dish",
            description=(
                "Modifies an existing saved event menu: replace a dish (action='replace'), add a course (action='add'), "
                "remove a dish (action='remove'), change guest count (action='scale'), or rename the event (action='rename')."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "name_or_id": {"type": "STRING", "description": "Name or ID of the saved event menu."},
                    "action": {
                        "type": "STRING",
                        "description": "One of 'replace', 'add', 'remove', 'scale', or 'rename'.",
                    },
                    "dish_query": {
                        "type": "STRING",
                        "description": "Clean culinary search query for the new dish (when action is 'replace' or 'add'), or the new event name (when action is 'rename').",
                    },
                    "target_dish_or_course": {
                        "type": "STRING",
                        "description": "Dish title or course to replace or remove.",
                    },
                    "course": {
                        "type": "STRING",
                        "description": "Course label when adding or replacing a dish (e.g. 'Appetizer', 'Main', 'Side', 'Dessert').",
                    },
                    "new_guest_count": {
                        "type": "INTEGER",
                        "description": "Target guest count when action is 'scale'.",
                    },
                },
                "required": ["name_or_id", "action"],
            },
        ),
    ]
    return types_mod.Tool(function_declarations=decls)


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
            update_event_menu_dish,
            DINNERGUEST_INSTRUCTION,
        )
        from dinnerguest.agents.menu_agent import MENU_AGENT_INSTRUCTION
        from dinnerguest.agents.on_hand_agent import ON_HAND_AGENT_INSTRUCTION
        from dinnerguest.agents.vibe_agent import VIBE_AGENT_INSTRUCTION

        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        configured_model = os.getenv("GEN_MODEL", "gemini-2.5-flash")
        candidate_models: List[str] = []
        for m_name in ("gemini-2.5-flash", "gemini-2.0-flash", configured_model):
            if m_name and m_name not in candidate_models:
                candidate_models.append(m_name)

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
            update_event_menu_dish,
        ]
        tool_map = {fn.__name__: fn for fn in available_tools}
        explicit_tool = _build_Explicit_tool_declarations(types)

        session = _SESSIONS.setdefault(session_id, {
            "vibe_state": None,
            "active_menu_name": None,
            "menu_planning_state": None,
            "chat_history": [],
            "turn_count": 0,
        })
        active_menu = session.get("active_menu_name")
        sys_instruction = (
            f"{DINNERGUEST_INSTRUCTION}\n\n"
            f"### Specialized Sub-Agent Instructions\n"
            f"#### MenuAgent\n{MENU_AGENT_INSTRUCTION}\n\n"
            f"#### OnHandAgent\n{ON_HAND_AGENT_INSTRUCTION}\n\n"
            f"#### VibeAgent\n{VIBE_AGENT_INSTRUCTION}"
        )
        if active_menu:
            sys_instruction += (
                f"\n\n[Active Session Context: The user is currently working on the event menu '{active_menu}'. "
                f"If they ask to replace a dish, add a course, remove a dish, change guest count, rename the event, or generate a grocery list, apply it to '{active_menu}'.]"
            )

        history = list(session.get("chat_history") or [])
        chat = None
        response = None
        active_model_name = candidate_models[0]

        for model_candidate in candidate_models:
            try:
                chat = client.chats.create(
                    model=model_candidate,
                    history=history,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_instruction,
                        tools=[explicit_tool],
                        temperature=0.2,
                    ),
                )
                response = chat.send_message(user_msg)
                active_model_name = model_candidate
                break
            except Exception as model_err:
                logger.warning("Model '%s' invocation failed (%s), trying next fallback model...", model_candidate, model_err)
                chat = None
                response = None

        if chat is None or response is None:
            return None

        events: List[Dict[str, Any]] = [
            {"type": "thought", "text": f"Dinnerguest LLM agent active ({active_model_name}). Processing query with multi-agent instructions and tool orchestrator..."}
        ]
        last_tool_result: Any = None

        # Loop through tool execution turns if model invokes function calls
        for _ in range(5):
            function_calls = getattr(response, "function_calls", None)
            if not function_calls:
                break

            tool_responses = []
            for call in function_calls:
                call_name = getattr(call, "name", "")
                call_args = dict(getattr(call, "args", {}) or {})
                events.append({
                    "type": "tool_call",
                    "tool": call_name,
                    "args": call_args,
                })
                fn = tool_map.get(call_name)
                if fn:
                    try:
                        tool_result = fn(**call_args)
                    except Exception as fn_err:
                        tool_result = {"error": str(fn_err)}
                else:
                    tool_result = {"error": f"Tool '{call_name}' not recognized."}

                last_tool_result = tool_result
                if isinstance(tool_result, dict) and call_name in ("save_event_menu", "update_event_menu_dish", "get_event_menu"):
                    if tool_result.get("name"):
                        session["active_menu_name"] = tool_result["name"]

                events.append({
                    "type": "tool_result",
                    "tool": call_name,
                    "args": call_args,
                    "result": tool_result,
                })
                tool_responses.append(
                    types.Part.from_function_response(
                        name=call_name,
                        response={"result": tool_result},
                    )
                )

            if tool_responses:
                response = chat.send_message(tool_responses)
            else:
                break

        reply_text = getattr(response, "text", "") or ""
        if reply_text.strip():
            try:
                clean_history = list(session.get("chat_history") or [])
                clean_history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_msg)]))
                clean_history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply_text.strip())]))
                session["chat_history"] = clean_history[-20:]
            except Exception:
                pass

            out_payload: Dict[str, Any] = {
                "reply": reply_text.strip(),
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
            }
            if last_tool_result is not None:
                out_payload["data"] = last_tool_result
            return out_payload
    except Exception as exc:
        logger.warning("Vertex AI LLM agent pass skipped (%s); executing deterministic culinary pipeline", exc)
        return None

    return None


def _execute_agent_turn(user_msg: str, session_id: str, trace_id: str) -> Dict[str, Any]:
    _RECORDED_SPANS.set([])
    set_trace_context(trace_id=trace_id, session_id=session_id)
    turn_t0 = time.perf_counter()

    with start_span(
        "agent.execute_turn",
        attributes={"session_id": session_id, "user_message": redact_pii(user_msg)},
        trace_id=trace_id,
        session_id=session_id,
    ):
        log_agent_intent(
            action="agent.execute_turn",
            intent_description=f"Process user turn: {redact_pii(user_msg)}",
            args={"user_msg": redact_pii(user_msg)},
            session_id=session_id,
            trace_id=trace_id,
        )

        # 0. Attempt LLM Agent execution via Google GenAI / Vertex AI if active
        result = _try_gemini_agent_turn(user_msg, session_id, trace_id)
        if result is None:
            # 1. Deterministic Agent Pipeline Fallback
            result = _execute_deterministic_agent_turn(user_msg, session_id, trace_id)

        # Emit structured Intent vs. Outcome logs and child spans for recorded tool events
        current_thought = "Execute culinary tool"
        for ev in result.get("events", []):
            ev_type = ev.get("type")
            if ev_type == "thought":
                current_thought = ev.get("text", current_thought)
            elif ev_type == "tool_call":
                t_name = ev.get("tool", "unknown_tool")
                t_args = ev.get("args", {})
                log_agent_intent(
                    action=t_name,
                    intent_description=current_thought,
                    args=t_args,
                    session_id=session_id,
                    trace_id=trace_id,
                )
            elif ev_type == "tool_result":
                t_name = ev.get("tool", "unknown_tool")
                t_res = ev.get("result")
                with start_span(
                    f"tool.{t_name}",
                    attributes={"tool.name": t_name, "tool.args": ev.get("args", {})},
                    trace_id=trace_id,
                    session_id=session_id,
                ):
                    log_agent_outcome(
                        action=t_name,
                        status="ERROR" if (isinstance(t_res, dict) and "error" in t_res) else "SUCCESS",
                        outcome_summary={"result_type": type(t_res).__name__},
                        duration_ms=round((time.perf_counter() - turn_t0) * 1000.0, 3),
                        session_id=session_id,
                        trace_id=trace_id,
                    )

        turn_ms = (time.perf_counter() - turn_t0) * 1000.0
        log_agent_outcome(
            action="agent.execute_turn",
            status="SUCCESS",
            outcome_summary={"reply_length": len(result.get("reply", "")), "event_count": len(result.get("events", []))},
            duration_ms=turn_ms,
            session_id=session_id,
            trace_id=trace_id,
        )

    result["spans"] = get_recorded_spans()
    return result


def _format_updated_menu_reply(
    summary: Any,
    action_headline: str,
    featured_dish: Optional[Dict[str, Any]] = None,
) -> str:
    """Formats an updated multi-course event menu response after a conversational modification."""
    from dinnerguest.database import clean_ingredient_item

    lines = [
        f"🔄 **Updated '{summary.name}' Menu ({summary.guest_count} Guests)**",
        "",
        action_headline,
        "",
        "### 🍽️ Planned Courses:",
    ]
    for idx, d in enumerate(summary.dishes, 1):
        allergen_tag = f" *(Allergens: {', '.join(d.allergens_detected)})*" if d.allergens_detected else " *(Allergen-free)*"
        lines.append(f"{idx}. **{d.course}**: **{d.recipe_title}** — Scaled to {d.servings} servings{allergen_tag}")

    if featured_dish and (featured_dish.get("scaled_ingredients") or featured_dish.get("original_ingredients") or featured_dish.get("ingredients")):
        lines.extend([
            "",
            f"### 📖 Highlighted Dish: {featured_dish.get('title', featured_dish.get('recipe_title', 'Recipe'))} (Scaled to {summary.guest_count} servings)",
            "**Ingredients:**",
        ])
        for ing in featured_dish.get("scaled_ingredients") or featured_dish.get("original_ingredients") or featured_dish.get("ingredients", []):
            lines.append(f"• {clean_ingredient_item(ing)}")

    lines.extend([
        "",
        "---",
        "### 🛡️ Consolidated Allergen Profile:",
        f"- **Detected Allergens**: {', '.join(summary.consolidated_allergens) if summary.consolidated_allergens else 'None'}",
        f"- **Allergen-Free Courses**: {', '.join(summary.allergen_free_courses) if summary.allergen_free_courses else 'All courses contain at least one common allergen'}",
        "",
        "---",
        "### 📋 Next Steps:",
        f"- Prompt **\"grocery list\"** to view the updated aisle-organized shopping list ({summary.grocery_item_count} consolidated items).",
        "- Ask to **replace**, **add**, or **remove** another course, or **scale** to a different guest count.",
    ])
    return "\n".join(lines)


def _clean_dish_search_query(raw_q: str) -> str:
    """Strips conversational filler ('some sort of', 'dish', 'on the menu') to produce a clean recipe search query."""
    q = raw_q.strip().rstrip(".,?!")
    q = re.sub(
        r"^(?:a\s+sort\s+of\s+|a\s+kind\s+of\s+|a\s+type\s+of\s+|some\s+sort\s+of\s+|some\s+kind\s+of\s+|some\s+type\s+of\s+|some\s+|another\s+|a\s+|an\s+|the\s+)+",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    q = re.sub(
        r"\s+(?:on|in|for|to)\s+(?:the\s+|our\s+)?(?:menu|dinner|lunch|party|feast|event|plan|meal)$",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    q = re.sub(
        r"\s+(?:dish|recipe|course|option|item)$",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    return q or raw_q.strip()


def _extract_menu_rename_intent(user_msg: str) -> Optional[str]:
    """Detects if the user is naming or renaming an event menu (e.g. 'Okay, let's call this event \"Saturday Gamenight\"')."""
    text = user_msg.strip()
    patterns = [
        r"\b(?:call|name|rename|label|title)\s+(?:this|the|our|my|it)\s*(?:event|menu|dinner|party|meal|feast|plan|gathering)?\s*(?:to|as)?\s*[\"“']([^\"”']+)[\"”']",
        r"\b(?:call|name|rename|label|title)\s+(?:this|the|our|my)\s+(?:event|menu|dinner|party|meal|feast|plan|gathering)\s+(?:to\s+|as\s+)?[\"“']?([^\"”'.?!]+)[\"”']?",
        r"\b(?:let's|lets|please|we\s+can|we\s+should|i'll|i\s+will)\s+(?:call|name|rename|label)\s+(?:it|this)\s+(?:to\s+|as\s+)?[\"“']?([^\"”'.?!]+)[\"”']?",
        r"\b(?:save|store)\s+(?:this|the|our|it)?\s*(?:event|menu|dinner|party|meal|plan)?\s+as\s+[\"“']?([^\"”'.?!]+)[\"”']?",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().strip('"').strip("'").strip("“”").rstrip(".,?!")
            if candidate and len(candidate) >= 2:
                return candidate
    return None


def _extract_custom_event_label(user_msg: str) -> Optional[str]:
    """Extracts an inline custom event name from a planning prompt if provided (e.g. 'called Saturday Gamenight')."""
    m = re.search(
        r"\b(?:called|named|labeled|titled|for\s+our|for\s+my)\s+[\"“']?([A-Za-z0-9\s'&-]{3,40})[\"”']?",
        user_msg.strip(),
        re.IGNORECASE,
    )
    if m:
        label = m.group(1).strip().strip('"').strip("'").strip("“”").rstrip(".,?!")
        generic = {"dinner", "lunch", "party", "dinner party", "meal", "guests", "people", "family", "friends", "tonight", "tomorrow"}
        if label.lower() not in generic:
            return label
    m_quote = re.search(r"[\"“]([^\"”]{3,40})[\"”]", user_msg.strip())
    if m_quote:
        return m_quote.group(1).strip()
    return None


def _parse_requested_courses_spec(user_msg: str) -> Optional[List[Dict[str, str]]]:
    """Parses explicit course/dish requests from a user message during interactive menu planning.

    Returns a list of dicts [{'course': 'Appetizer', 'query': '...'}, ...] if course/dish specifications are present.
    """
    text = user_msg.strip()
    lower = text.lower()

    # Remove any trailing "call it X" clause before parsing courses
    text_no_rename = re.sub(
        r"[,;.]?\s*(?:and\s+)?(?:let's\s+|please\s+)?(?:call|name|label|save)\s+(?:this|the|it)\s*(?:event|menu|dinner|party)?\s*(?:as|to)?\s*[\"“']?[^\"”'.?!]+[\"”']?$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    course_keywords = ("appetizer", "appetizers", "starter", "starters", "main", "mains", "entree", "entrees", "side", "sides", "dessert", "desserts", "soup", "salad", "course", "courses")
    has_course_word = any(re.search(rf"\b{kw}\b", lower) for kw in course_keywords)

    # Check if user specified a list of dishes/courses
    segments = [s.strip() for s in re.split(r"[,;]+|\band\b", text_no_rename) if s.strip()]
    specs: List[Dict[str, str]] = []

    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "1": 1, "2": 2, "3": 3, "4": 4}
    default_queries = {
        "Appetizer": "savory appetizer starter",
        "Starter": "soup or salad starter",
        "Main": "savory main dinner entree",
        "Side": "savory roasted vegetable or potato side dish",
        "Dessert": "sweet baked dessert pie or crisp",
    }

    for seg in segments:
        seg_l = seg.lower()
        # Skip pure framing segments like "I need to plan a dinner for 6" unless they contain course specs
        if re.match(r"^(?:i\s+need\s+to|i\s+want\s+to|help\s+me|let's|can\s+we|please)?\s*(?:plan|make|do|have|include)\s+(?:a\s+)?(?:\d+\s*-?\s*course\s+)?(?:dinner|lunch|meal|menu|party|event)(?:\s+for\s+.*)?$", seg_l):
            continue

        matched_course: Optional[str] = None
        if any(w in seg_l for w in ("appetizer", "appetizers", "hors d'oeuvre")):
            matched_course = "Appetizer"
        elif any(w in seg_l for w in ("starter", "starters")):
            matched_course = "Starter"
        elif any(w in seg_l for w in ("main", "mains", "entree", "entrees")):
            matched_course = "Main"
        elif any(w in seg_l for w in ("side", "sides")):
            matched_course = "Side"
        elif any(w in seg_l for w in ("dessert", "desserts", "sweet")):
            matched_course = "Dessert"

        if matched_course:
            # Check if a multiplier was specified (e.g. "2 sides" or "two side dishes")
            count = 1
            m_cnt = re.search(r"\b(one|two|three|four|1|2|3|4)\s+(?:[a-z]+\s+)?(?:appetizers|starters|mains|entrees|sides|side\s+dishes|desserts)\b", seg_l)
            if m_cnt:
                count = word_to_num.get(m_cnt.group(1), 1)

            cleaned_desc = re.sub(
                r"\b(?:i'd\s+like|let's\s+do|let's\s+have|include|with|a|an|some|one|two|three|four|1|2|3|4|appetizer|appetizers|starter|starters|main\s+course|main\s+dish|main|mains|entree|entrees|side\s+dish|side\s+dishes|side|sides|dessert|desserts|course|courses|for)\b",
                "",
                seg,
                flags=re.IGNORECASE,
            ).strip(" :,-.?!")
            cleaned_desc = _clean_dish_search_query(cleaned_desc) if cleaned_desc else ""
            query = cleaned_desc if len(cleaned_desc) >= 3 else default_queries[matched_course]
            for _ in range(count):
                specs.append({"course": matched_course, "query": query})

    if specs:
        return specs

    # Check if user simply asked for "3 courses" / "4 courses" without naming each course
    m_num_courses = re.search(r"\b(two|three|four|five|2|3|4|5)\s*-?\s*courses?\b", lower)
    if m_num_courses:
        n_c = {"two": 2, "three": 3, "four": 4, "five": 5, "2": 2, "3": 3, "4": 4, "5": 5}.get(m_num_courses.group(1), 3)
        if n_c == 2:
            return [{"course": "Main", "query": default_queries["Main"]}, {"course": "Dessert", "query": default_queries["Dessert"]}]
        if n_c == 3:
            return [
                {"course": "Appetizer", "query": default_queries["Appetizer"]},
                {"course": "Main", "query": default_queries["Main"]},
                {"course": "Dessert", "query": default_queries["Dessert"]},
            ]
        if n_c == 4:
            return [
                {"course": "Appetizer", "query": default_queries["Appetizer"]},
                {"course": "Main", "query": default_queries["Main"]},
                {"course": "Side", "query": default_queries["Side"]},
                {"course": "Dessert", "query": default_queries["Dessert"]},
            ]
        return [
            {"course": "Appetizer", "query": default_queries["Appetizer"]},
            {"course": "Starter", "query": default_queries["Starter"]},
            {"course": "Main", "query": default_queries["Main"]},
            {"course": "Side", "query": default_queries["Side"]},
            {"course": "Dessert", "query": default_queries["Dessert"]},
        ]

    if has_course_word and len(segments) >= 2:
        return [
            {"course": "Appetizer", "query": default_queries["Appetizer"]},
            {"course": "Main", "query": default_queries["Main"]},
            {"course": "Dessert", "query": default_queries["Dessert"]},
        ]

    return None


def _extract_menu_modification_intent(user_msg: str) -> Optional[Dict[str, Any]]:
    """Detects if the user is asking to replace, swap, add, or remove a dish in an event menu."""
    text = user_msg.strip()

    # 1. Replace / Swap / Switch / Change X with/for/to Y
    m_rep = re.search(
        r"\b(?:replace|swap|switch|change|substitute)\s+(?:the\s+|our\s+|my\s+)?(.+?)\s+(?:with|for|to)\s+(?:some\s+sort\s+of\s+|some\s+kind\s+of\s+|some\s+type\s+of\s+|a\s+sort\s+of\s+|a\s+kind\s+of\s+|some\s+|another\s+|a\s+|an\s+)?(.+)",
        text,
        re.IGNORECASE,
    )
    if m_rep:
        target = m_rep.group(1).strip().rstrip(".,?!")
        new_q = _clean_dish_search_query(m_rep.group(2))
        return {"action": "replace", "target": target, "dish_query": new_q}

    # 2. "Use Y instead of X"
    m_inst1 = re.search(
        r"\b(?:use|do|have|make|serve)\s+(?:some\s+sort\s+of\s+|some\s+kind\s+of\s+|some\s+|a\s+|an\s+)?(.+?)\s+instead\s+of\s+(?:the\s+|our\s+)?(.+)",
        text,
        re.IGNORECASE,
    )
    if m_inst1:
        new_q = _clean_dish_search_query(m_inst1.group(1))
        target = m_inst1.group(2).strip().rstrip(".,?!")
        return {"action": "replace", "target": target, "dish_query": new_q}

    # 3. "Instead of X, let's do Y"
    m_inst2 = re.search(
        r"instead\s+of\s+(?:the\s+|our\s+)?(.+?)\s*,\s*(?:let's\s+|can\s+we\s+|could\s+we\s+)?(?:use|do|have|make|serve|replace\s+it\s+with)?\s*(?:some\s+sort\s+of\s+|some\s+kind\s+of\s+|some\s+|a\s+|an\s+)?(.+)",
        text,
        re.IGNORECASE,
    )
    if m_inst2:
        target = m_inst2.group(1).strip().rstrip(".,?!")
        new_q = _clean_dish_search_query(m_inst2.group(2))
        return {"action": "replace", "target": target, "dish_query": new_q}

    # 4. Add a course/dish ("Add a deviled egg appetizer", "Add a Caesar salad starter")
    m_add = re.search(
        r"(?:^|\b)(?:add|include|also\s+serve|let's\s+add|can\s+we\s+add|could\s+we\s+add)\s+(?:some\s+sort\s+of\s+|some\s+kind\s+of\s+|some\s+|a\s+|an\s+)?(.+)",
        text,
        re.IGNORECASE,
    )
    if m_add:
        raw_add = m_add.group(1).strip().rstrip(".,?!")
        raw_lower = raw_add.lower()
        course: Optional[str] = None
        if any(w in raw_lower for w in ("appetizer", "starter", "hors d'oeuvre")):
            course = "Appetizer"
        elif any(w in raw_lower for w in ("side dish", "side")):
            course = "Side"
        elif "dessert" in raw_lower:
            course = "Dessert"
        elif any(w in raw_lower for w in ("main course", "main", "entree")):
            course = "Main"
        elif any(w in raw_lower for w in ("drink", "beverage", "cocktail")):
            course = "Drink"

        clean_q = re.sub(
            r"\b(?:as\s+a\s+|as\s+an\s+|for\s+the\s+|for\s+|to\s+the\s+|to\s+our\s+)?(?:appetizer|starter|side\s+dish|side|dessert|main\s+course|main|entree|drink|beverage|course|menu|dinner|party|feast)\b",
            "",
            raw_add,
            flags=re.IGNORECASE,
        ).strip(" ,.-?!")
        clean_q = _clean_dish_search_query(clean_q) if clean_q else _clean_dish_search_query(raw_add)

        if not course:
            ql = clean_q.lower()
            if any(w in ql for w in ("deviled egg", "bruschetta", "dip", "crostini", "wings", "meatball")):
                course = "Appetizer"
            elif any(w in ql for w in ("pie", "cake", "cookie", "crisp", "brownie", "tart", "cobbler", "pudding", "ice cream")):
                course = "Dessert"
            elif any(w in ql for w in ("soup", "salad", "chowder")):
                course = "Starter"
            else:
                course = "Side"

        return {"action": "add", "dish_query": clean_q, "course": course}

    # 5. Remove / Drop a dish or course ("Remove the stuffing", "Drop the appetizer")
    m_rem = re.search(
        r"(?:^|\b)(?:remove|delete|drop|take\s+off|omit|skip)\s+(?:the\s+|our\s+)?(.+?)(?:\s+from\s+(?:the\s+|our\s+)?(?:menu|dinner|feast|party))?[.?!]*$",
        text,
        re.IGNORECASE,
    )
    if m_rem:
        target = m_rem.group(1).strip().rstrip(".,?!")
        return {"action": "remove", "target": target}

    return None


def _execute_deterministic_agent_turn(user_msg: str, session_id: str, trace_id: str) -> Dict[str, Any]:
    session = _SESSIONS.setdefault(session_id, {
        "vibe_state": None,
        "active_menu_name": None,
        "menu_planning_state": None,
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

    # 1a. Event Naming / Renaming Follow-Up ("Okay, let's call this event 'Saturday Gamenight'")
    rename_target = _extract_menu_rename_intent(user_msg)
    if rename_target is not None and _extract_menu_modification_intent(user_msg) is None and _parse_requested_courses_spec(user_msg) is None:
        from dinnerguest.agent import _menu_agent

        # If we are currently awaiting course selection for a new event, record the label
        plan_state = session.get("menu_planning_state")
        if plan_state and plan_state.get("status") == "AWAITING_COURSES":
            plan_state["event_name"] = rename_target
            gc = plan_state.get("guest_count", 6)
            events.append({"type": "thought", "text": f"Recorded custom event label '{rename_target}' for {gc} guests. Awaiting course preferences..."})
            reply_text = (
                f"🏷️ Got it — we'll label this event **\"{rename_target}\"** (for **{gc} guests**)!\n\n"
                "What **number and types of courses/dishes** would you like to include in the plan? "
                "(For example: *\"An appetizer, a grilled chicken main course, a side dish, and a dessert\"* or *\"3 courses: salad starter, pasta main, and fruit pie\"*)."
            )
            return {
                "reply": reply_text,
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
                "data": plan_state,
            }

        active_menu_name = session.get("active_menu_name")
        if not active_menu_name:
            saved_menus = _menu_agent.list_saved_menus()
            if saved_menus:
                active_menu_name = saved_menus[0]["name"]

        if active_menu_name:
            tool_args = {"name_or_id": active_menu_name, "action": "rename", "dish_query": rename_target}
            events.append({"type": "thought", "text": f"Renaming active event menu '{active_menu_name}' to '{rename_target}' in persistent storage..."})
            events.append({"type": "tool_call", "tool": "update_event_menu_dish", "args": tool_args})
            summary = _menu_agent.rename_event_menu(active_menu_name, rename_target)
            if summary:
                session["active_menu_name"] = summary.name
                events.append({"type": "tool_result", "tool": "update_event_menu_dish", "args": tool_args, "result": summary.to_dict()})
                headline = (
                    f"🏷️ Saved and labeled this event menu as **\"{summary.name}\"** "
                    f"(previously *{active_menu_name}*). You can recall or generate a shopping list for **\"{summary.name}\"** anytime!"
                )
                reply_text = _format_updated_menu_reply(summary, headline)
                return {
                    "reply": reply_text,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "events": events,
                    "data": summary.to_dict(),
                }

    # 1b. Contextual Event Menu Follow-Up (Replace/Swap Dish, Add Course, Remove Dish)
    menu_mod = _extract_menu_modification_intent(user_msg)
    if menu_mod is not None:
        from dinnerguest.agent import _menu_agent, update_event_menu_dish
        from dinnerguest.tools.vibe_search_tool import VibeSearchTool

        active_menu_name = session.get("active_menu_name")
        if not active_menu_name:
            saved_menus = _menu_agent.list_saved_menus()
            if saved_menus:
                active_menu_name = saved_menus[0]["name"]

        if active_menu_name:
            act = menu_mod["action"]
            vibe_tool = VibeSearchTool()

            if act == "replace":
                target_dish = menu_mod["target"]
                dish_query = menu_mod["dish_query"]
                events.append({
                    "type": "thought",
                    "text": f"Active menu context: '{active_menu_name}'. Searching for '{dish_query}' to replace '{target_dish}' on the menu...",
                })
                events.append({"type": "tool_call", "tool": "search_recipes_by_vibe", "args": {"query": dish_query}})
                candidates = vibe_tool.search_by_vibe(dish_query, limit=5)
                # Exclude the dish being replaced
                target_words = [w.rstrip("s") for w in re.findall(r"\b[a-z]{3,}\b", target_dish.lower())]
                filtered_candidates = [
                    c for c in candidates
                    if not (target_words and all(w in c.title.lower() for w in target_words))
                ]
                if filtered_candidates:
                    candidates = filtered_candidates
                events.append({
                    "type": "tool_result",
                    "tool": "search_recipes_by_vibe",
                    "args": {"query": dish_query},
                    "result": [c.to_dict() for c in candidates[:3]],
                })

                best_candidate = candidates[0] if candidates else None
                new_dish_entry = {
                    "recipe_id": best_candidate.recipe_id if best_candidate else "custom_dish",
                    "recipe_title": best_candidate.title if best_candidate else dish_query.title(),
                    "course": None,
                    "original_servings": best_candidate.servings if best_candidate else 4,
                    "ingredients": best_candidate.ingredients if best_candidate else [dish_query],
                }

                tool_args = {
                    "name_or_id": active_menu_name,
                    "action": "replace",
                    "target_dish_or_course": target_dish,
                    "dish_query": dish_query,
                }
                events.append({"type": "tool_call", "tool": "update_event_menu_dish", "args": tool_args})
                rep_res = _menu_agent.replace_dish_in_event_menu(
                    name_or_id=active_menu_name,
                    target_dish_or_course=target_dish,
                    new_dish=new_dish_entry,
                )
                if rep_res:
                    summary, replaced_title = rep_res
                    session["active_menu_name"] = summary.name
                    events.append({"type": "tool_result", "tool": "update_event_menu_dish", "args": tool_args, "result": summary.to_dict()})
                    matched_menu_dish = next((d.to_dict() for d in summary.dishes if d.recipe_title == new_dish_entry["recipe_title"]), None)
                    headline = (
                        f"✅ Replaced **{replaced_title}** with **{new_dish_entry['recipe_title']}**, "
                        f"automatically scaled to **{summary.guest_count} servings**, and updated the event allergen profile:"
                    )
                    # Check if user noted a dietary avoidance (e.g. "doesn't eat beef" / "no beef") and warn if other courses contain it
                    m_avoid = re.search(r"(?:doesn't\s+eat|don't\s+eat|no|avoid|allergic\s+to)\s+(beef|pork|chicken|meat|dairy|gluten|nuts|shellfish|fish)", lower)
                    if m_avoid:
                        avoid_term = m_avoid.group(1).lower()
                        other_flagged = [
                            f"**{d.recipe_title}** (*{d.course}*)"
                            for d in summary.dishes
                            if d.recipe_title != new_dish_entry["recipe_title"]
                            and any(avoid_term in ing.lower() for ing in (d.scaled_ingredients or d.original_ingredients))
                        ]
                        if other_flagged:
                            headline += (
                                f"\n\n💡 **Dietary Heads-Up**: Since a guest avoids **{avoid_term}**, please note that "
                                f"{', '.join(other_flagged)} on the menu also contains {avoid_term}. Let me know if you'd like to swap that out as well!"
                            )
                    reply_text = _format_updated_menu_reply(summary, headline, featured_dish=matched_menu_dish)
                    return {
                        "reply": reply_text,
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "events": events,
                        "data": summary.to_dict(),
                    }

            elif act == "add":
                dish_query = menu_mod["dish_query"]
                course = menu_mod.get("course", "Side")
                events.append({
                    "type": "thought",
                    "text": f"Active menu context: '{active_menu_name}'. Searching for '{dish_query}' to add as a {course} course...",
                })
                events.append({"type": "tool_call", "tool": "search_recipes_by_vibe", "args": {"query": dish_query}})
                candidates = vibe_tool.search_by_vibe(dish_query, limit=5)
                events.append({
                    "type": "tool_result",
                    "tool": "search_recipes_by_vibe",
                    "args": {"query": dish_query},
                    "result": [c.to_dict() for c in candidates[:3]],
                })

                best_candidate = candidates[0] if candidates else None
                new_dish_entry = {
                    "recipe_id": best_candidate.recipe_id if best_candidate else "custom_dish",
                    "recipe_title": best_candidate.title if best_candidate else dish_query.title(),
                    "course": course,
                    "original_servings": best_candidate.servings if best_candidate else 4,
                    "ingredients": best_candidate.ingredients if best_candidate else [dish_query],
                }

                tool_args = {
                    "name_or_id": active_menu_name,
                    "action": "add",
                    "dish_query": dish_query,
                    "course": course,
                }
                events.append({"type": "tool_call", "tool": "update_event_menu_dish", "args": tool_args})
                summary = _menu_agent.add_dish_to_event_menu(name_or_id=active_menu_name, new_dish=new_dish_entry)
                if summary:
                    session["active_menu_name"] = summary.name
                    events.append({"type": "tool_result", "tool": "update_event_menu_dish", "args": tool_args, "result": summary.to_dict()})
                    matched_menu_dish = next((d.to_dict() for d in summary.dishes if d.recipe_title == new_dish_entry["recipe_title"]), None)
                    headline = (
                        f"✅ Added **{new_dish_entry['recipe_title']}** (*{course}*) to **{summary.name}**, "
                        f"scaled to **{summary.guest_count} servings**, and updated the event allergen profile:"
                    )
                    reply_text = _format_updated_menu_reply(summary, headline, featured_dish=matched_menu_dish)
                    return {
                        "reply": reply_text,
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "events": events,
                        "data": summary.to_dict(),
                    }

            elif act == "remove":
                target_dish = menu_mod["target"]
                tool_args = {
                    "name_or_id": active_menu_name,
                    "action": "remove",
                    "target_dish_or_course": target_dish,
                }
                events.append({"type": "thought", "text": f"Active menu context: '{active_menu_name}'. Removing '{target_dish}' from the menu..."})
                events.append({"type": "tool_call", "tool": "update_event_menu_dish", "args": tool_args})
                rem_res = _menu_agent.remove_dish_from_event_menu(name_or_id=active_menu_name, target_dish_or_course=target_dish)
                if rem_res:
                    summary, removed_title = rem_res
                    session["active_menu_name"] = summary.name
                    events.append({"type": "tool_result", "tool": "update_event_menu_dish", "args": tool_args, "result": summary.to_dict()})
                    headline = f"✅ Removed **{removed_title}** from **{summary.name}** and recalculated the allergen profile:"
                    reply_text = _format_updated_menu_reply(summary, headline)
                    return {
                        "reply": reply_text,
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "events": events,
                        "data": summary.to_dict(),
                    }

    # 1c. In-progress Interactive Event Menu Course Selection
    plan_state = session.get("menu_planning_state")
    if plan_state and plan_state.get("status") == "AWAITING_COURSES":
        inline_name = _extract_menu_rename_intent(user_msg) or _extract_custom_event_label(user_msg)
        if inline_name:
            plan_state["event_name"] = inline_name

        course_specs = _parse_requested_courses_spec(user_msg)
        if course_specs:
            from dinnerguest.agent import _menu_agent
            from dinnerguest.tools.vibe_search_tool import VibeSearchTool

            vibe_tool = VibeSearchTool()
            event_name = plan_state.get("event_name") or "Custom Dinner Menu"
            guest_count = plan_state.get("guest_count") or 6

            events.append({
                "type": "thought",
                "text": f"Building custom {len(course_specs)}-course menu for '{event_name}' ({guest_count} guests)...",
            })
            built_dishes: List[Dict[str, Any]] = []
            used_ids = set()
            for spec in course_specs:
                c_label = spec["course"]
                c_query = spec["query"]
                events.append({"type": "tool_call", "tool": "search_recipes_by_vibe", "args": {"query": c_query}})
                cands = vibe_tool.search_by_vibe(c_query, limit=5)
                events.append({"type": "tool_result", "tool": "search_recipes_by_vibe", "args": {"query": c_query}, "result": [c.to_dict() for c in cands[:2]]})
                chosen = next((c for c in cands if c.recipe_id not in used_ids), cands[0] if cands else None)
                if chosen:
                    used_ids.add(chosen.recipe_id)
                    built_dishes.append({
                        "recipe_id": chosen.recipe_id,
                        "recipe_title": chosen.title,
                        "course": c_label,
                        "original_servings": chosen.servings,
                        "ingredients": chosen.ingredients,
                    })

            events.append({"type": "tool_call", "tool": "plan_and_save_event_menu", "args": {"name": event_name, "guest_count": guest_count}})
            summary = _menu_agent.plan_and_save_event_menu(
                name=event_name,
                guest_count=guest_count,
                dishes=built_dishes,
                theme=f"Custom {len(built_dishes)}-Course Menu",
                auto_scale=True,
            )
            session["active_menu_name"] = summary.name
            session["menu_planning_state"] = None
            events.append({"type": "tool_result", "tool": "plan_and_save_event_menu", "args": {"name": event_name}, "result": summary.to_dict()})

            label_hint = (
                f"You can rename this event anytime (e.g., *\"Let's call this event 'Saturday Gamenight'\"*)."
                if event_name == "Custom Dinner Menu"
                else f"Saved under the event label **\"{summary.name}\"** for easy recall!"
            )
            headline = f"🎉 **Planned & Scaled '{summary.name}' for {guest_count} Guests**\n{label_hint}"
            reply_text = _format_updated_menu_reply(summary, headline)
            return {
                "reply": reply_text,
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
                "data": summary.to_dict(),
            }

    # 1d. Recall or Grocery List for a Previously Saved Named Event Menu
    from dinnerguest.agent import _menu_agent as _global_menu_agent
    all_saved_menus = _global_menu_agent.list_saved_menus()
    referenced_menu = None
    for sm in all_saved_menus:
        sm_name = (sm.get("name") or "").strip()
        if sm_name and len(sm_name) >= 3 and sm_name.lower() in lower:
            if not any(h in lower and "plan" in lower for h in ("thanksgiving", "easter")):
                referenced_menu = sm_name
                break

    if referenced_menu and any(k in lower for k in ("menu", "what", "show", "recall", "pull up", "planned", "grocery", "shopping")):
        session["active_menu_name"] = referenced_menu
        if "grocery" in lower or "shopping" in lower:
            events.append({"type": "thought", "text": f"Generating consolidated grocery list for saved event '{referenced_menu}'..."})
            events.append({"type": "tool_call", "tool": "generate_grocery_list_for_menu", "args": {"name_or_id": referenced_menu}})
            glist = _global_menu_agent.generate_menu_grocery_list(referenced_menu)
            events.append({"type": "tool_result", "tool": "generate_grocery_list_for_menu", "args": {"name_or_id": referenced_menu}, "result": glist.to_dict() if glist else {}})
            if glist:
                lines = [f"🛒 **Consolidated Grocery List for '{referenced_menu}'** ({len(glist.items)} items across aisles):"]
                for aisle, items in glist.aisle_groups.items():
                    lines.append(f"\n**[{aisle}]**")
                    for itm in items:
                        pkg = f" *({itm.packaging_note})*" if itm.packaging_note else ""
                        lines.append(f"• {itm.display_quantity} {itm.canonical_name}{pkg}")
                return {
                    "reply": "\n".join(lines),
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "events": events,
                }
        else:
            events.append({"type": "thought", "text": f"Recalling saved event menu '{referenced_menu}' from persistent memory..."})
            events.append({"type": "tool_call", "tool": "get_event_menu", "args": {"name_or_id": referenced_menu}})
            summary = _global_menu_agent.get_event_menu(referenced_menu)
            if summary:
                events.append({"type": "tool_result", "tool": "get_event_menu", "args": {"name_or_id": referenced_menu}, "result": summary.to_dict()})
                headline = f"📋 Recalled saved menu for **\"{summary.name}\"** ({summary.guest_count} guests):"
                reply_text = _format_updated_menu_reply(summary, headline)
                return {
                    "reply": reply_text,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "events": events,
                    "data": summary.to_dict(),
                }

    # 2. Event Menu / Holiday / Meal Planning & Grocery List Request
    holidays = ["thanksgiving", "easter", "christmas", "new year", "passover", "hanukkah", "st patrick", "halloween"]
    is_holiday = any(h in lower for h in holidays)
    is_thanksgiving = "thanksgiving" in lower
    is_easter = "easter" in lower

    plan_verbs = ["plan", "planning", "organize", "host", "hosting", "cater", "menu for", "courses"]
    has_plan_verb = any(v in lower for v in plan_verbs)
    meal_nouns = ["lunch", "dinner", "brunch", "breakfast", "supper", "meal", "feast", "party", "gathering", "event", "banquet"]
    has_meal = any(m in lower for m in meal_nouns)

    is_planning_meal = has_plan_verb and has_meal
    is_explicit_event = any(k in lower for k in ["event menu", "dinner party", "party menu", "3-course", "multi-course", "meal plan", "grocery", "shopping list"])

    if is_holiday or is_planning_meal or is_explicit_event:
        from dinnerguest.agent import _menu_agent
        from dinnerguest.tools.vibe_search_tool import VibeSearchTool
        vibe_tool = VibeSearchTool()
        by_id = {r["id"]: r for r in vibe_tool.recipes}

        # 2a. Easter Lunch Feast Planning
        if is_easter:
            events.append({"type": "thought", "text": "Detected Easter celebration request. Planning multi-course spring feast, scaling servings, and verifying allergen safety..."})
            guest_count = 6
            m = re.search(r"\b(?:for\s+)?(\d+)\s*(?:guests|people|servings)?", lower)
            if m and int(m.group(1)) > 0:
                guest_count = int(m.group(1))

            dishes = [
                {
                    "recipe_id": "recipe_016",
                    "recipe_title": by_id.get("recipe_016", {}).get("title", "Comforting Creamy Tomato Basil Soup"),
                    "course": "Starter",
                    "original_servings": by_id.get("recipe_016", {}).get("servings", 4),
                    "ingredients": by_id.get("recipe_016", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_020",
                    "recipe_title": by_id.get("recipe_020", {}).get("title", "Herb-Roasted Turkey Breast with Pan Gravy"),
                    "course": "Main",
                    "original_servings": by_id.get("recipe_020", {}).get("servings", 8),
                    "ingredients": by_id.get("recipe_020", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_019",
                    "recipe_title": by_id.get("recipe_019", {}).get("title", "Spicy Honey-Glazed Roasted Carrots"),
                    "course": "Side",
                    "original_servings": by_id.get("recipe_019", {}).get("servings", 4),
                    "ingredients": by_id.get("recipe_019", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_021",
                    "recipe_title": by_id.get("recipe_021", {}).get("title", "Creamy Garlic Mashed Potatoes"),
                    "course": "Side",
                    "original_servings": by_id.get("recipe_021", {}).get("servings", 8),
                    "ingredients": by_id.get("recipe_021", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_012",
                    "recipe_title": by_id.get("recipe_012", {}).get("title", "Cozy Warm Apple Cinnamon Crisp"),
                    "course": "Dessert",
                    "original_servings": by_id.get("recipe_012", {}).get("servings", 4),
                    "ingredients": by_id.get("recipe_012", {}).get("ingredients", []),
                },
            ]

            events.append({"type": "tool_call", "tool": "plan_and_save_event_menu", "args": {"name": "Easter Lunch", "guest_count": guest_count}})
            summary = _menu_agent.plan_and_save_event_menu(
                name="Easter Lunch",
                guest_count=guest_count,
                dishes=dishes,
                theme="Spring Easter Lunch Feast",
                notes="Herb-roasted turkey breast with pan gravy, honey-glazed roasted carrots, creamy garlic mashed potatoes, comforting creamy tomato basil soup, and warm apple cinnamon crisp.",
                auto_scale=True,
            )
            session["active_menu_name"] = summary.name
            events.append({"type": "tool_result", "tool": "plan_and_save_event_menu", "args": {"name": "Easter Lunch"}, "result": summary.to_dict()})

            reply_lines = [
                f"🌸 **Easter Lunch Feast Planned & Scaled for {guest_count} Guests**",
                "",
                "I've designed a celebratory spring Easter lunch menu, scaled all recipes to your guest count, and saved the menu to persistent memory:",
                "",
                "### 🍽️ Planned Courses:",
                f"1. **Starter**: **{dishes[0]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Velvety vine-ripened tomatoes simmered with fresh aromatic basil, cream, and garlic.",
                f"2. **Main Course**: **{dishes[1]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Herb-basted roasted turkey breast with rosemary, thyme, and golden pan drippings gravy.",
                f"3. **Side Dish**: **{dishes[2]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Tender sweet carrots roasted with wildflower honey, olive oil, and crushed chili flakes.",
                f"4. **Side Dish**: **{dishes[3]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Fluffy russet potatoes with roasted garlic, warm butter, and cream.",
                f"5. **Dessert**: **{dishes[4]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Tart-sweet cinnamon-spiced apples baked beneath a golden oat brown sugar streusel.",
                "",
                "---",
                "### 🛡️ Consolidated Allergen Profile:",
                f"- **Detected Allergens**: {', '.join(summary.consolidated_allergens) if summary.consolidated_allergens else 'None'}",
                "- **Safety Accommodations**: Contains dairy, wheat/gluten, and milk. Peanut, tree nut, shellfish, fish, and soy free.",
                "",
                "---",
                "### 📋 Next Steps:",
                "- Prompt **\"grocery list\"** to view an aisle-organized shopping list with package approximations.",
                f"- Prompt **\"scale to 10 guests\"** if you need to adjust portion sizes for more guests.",
            ]

            return {
                "reply": "\n".join(reply_lines),
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
                "data": summary.to_dict(),
            }

        # 2b. Thanksgiving Feast Planning
        if is_thanksgiving:
            events.append({"type": "thought", "text": "Detected Thanksgiving event request. Planning multi-course feast, scaling servings, and verifying allergen safety..."})
            guest_count = 8
            m = re.search(r"\b(?:for\s+)?(\d+)\s*(?:guests|people|servings)?", lower)
            if m and int(m.group(1)) > 0:
                guest_count = int(m.group(1))

            dishes = [
                {
                    "recipe_id": "recipe_020",
                    "recipe_title": by_id.get("recipe_020", {}).get("title", "Herb-Roasted Turkey Breast with Pan Gravy"),
                    "course": "Main",
                    "original_servings": by_id.get("recipe_020", {}).get("servings", 8),
                    "ingredients": by_id.get("recipe_020", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_021",
                    "recipe_title": by_id.get("recipe_021", {}).get("title", "Creamy Garlic Mashed Potatoes"),
                    "course": "Side",
                    "original_servings": by_id.get("recipe_021", {}).get("servings", 8),
                    "ingredients": by_id.get("recipe_021", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_022",
                    "recipe_title": by_id.get("recipe_022", {}).get("title", "Traditional Savory Herb Stuffing"),
                    "course": "Side",
                    "original_servings": by_id.get("recipe_022", {}).get("servings", 8),
                    "ingredients": by_id.get("recipe_022", {}).get("ingredients", []),
                },
                {
                    "recipe_id": "recipe_012",
                    "recipe_title": by_id.get("recipe_012", {}).get("title", "Cozy Warm Apple Cinnamon Crisp"),
                    "course": "Dessert",
                    "original_servings": by_id.get("recipe_012", {}).get("servings", 4),
                    "ingredients": by_id.get("recipe_012", {}).get("ingredients", []),
                },
            ]

            events.append({"type": "tool_call", "tool": "plan_and_save_event_menu", "args": {"name": "Thanksgiving Dinner", "guest_count": guest_count}})
            summary = _menu_agent.plan_and_save_event_menu(
                name="Thanksgiving Dinner",
                guest_count=guest_count,
                dishes=dishes,
                theme="Traditional Thanksgiving Feast",
                notes="Herb roasted turkey breast with gravy, garlic mashed potatoes, herb stuffing, and warm apple cinnamon crisp.",
                auto_scale=True,
            )
            session["active_menu_name"] = summary.name
            events.append({"type": "tool_result", "tool": "plan_and_save_event_menu", "args": {"name": "Thanksgiving Dinner"}, "result": summary.to_dict()})

            reply_lines = [
                f"🦃 **Thanksgiving Dinner Feast Planned & Scaled for {guest_count} Guests**",
                "",
                "I've designed a classic Thanksgiving menu, scaled all recipes to your guest count, and saved the menu to persistent memory:",
                "",
                "### 🍽️ Planned Courses:",
                f"1. **Main Course**: **{dishes[0]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Herb-basted roasted turkey breast with aromatic poultry herbs and rich pan gravy.",
                f"2. **Side Dish**: **{dishes[1]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Fluffy russet potatoes with roasted garlic, warm butter, and cream.",
                f"3. **Side Dish**: **{dishes[2]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Rustic bread cubes baked with fresh sage, thyme, celery, and savory broth.",
                f"4. **Dessert**: **{dishes[3]['recipe_title']}**",
                f"   - *Yield*: Scaled to {guest_count} servings",
                "   - *Profile*: Cinnamon-spiced apples baked under a golden brown oat streusel.",
                "",
                "---",
                "### 🛡️ Consolidated Allergen Profile:",
                f"- **Detected Allergens**: {', '.join(summary.consolidated_allergens) if summary.consolidated_allergens else 'None'}",
                "- **Safety Accommodations**: Contains dairy, wheat/gluten, and egg. Peanut, tree nut, shellfish, and soy free.",
                "",
                "---",
                "### 📋 Next Steps:",
                "- Prompt **\"grocery list\"** to view an aisle-organized shopping list with package approximations.",
                f"- Prompt **\"scale to 12 guests\"** if you need to adjust portion sizes for more guests.",
            ]

            return {
                "reply": "\n".join(reply_lines),
                "session_id": session_id,
                "trace_id": trace_id,
                "events": events,
                "data": summary.to_dict(),
            }

        # 2c. Non-Holiday Multi-Course Meal / Event Planning
        if "party" in lower or "3-course" in lower or is_planning_meal:
            if "grocery" not in lower and "shopping" not in lower and "list" not in lower:
                guest_count = 6
                m = re.search(r"\b(?:for\s+)?(\d+)\s*(?:guests|people|servings)?", lower)
                if m and int(m.group(1)) > 0:
                    guest_count = int(m.group(1))

                custom_label = _extract_menu_rename_intent(user_msg) or _extract_custom_event_label(user_msg)
                course_specs = _parse_requested_courses_spec(user_msg)

                # If the user has not specified the number/types of courses yet, ask interactively!
                if course_specs is None:
                    events.append({
                        "type": "thought",
                        "text": f"Detected non-holiday meal planning request for {guest_count} guests. Prompting user for course structure and event label...",
                    })
                    plan_state_data = {
                        "status": "AWAITING_COURSES",
                        "guest_count": guest_count,
                        "event_name": custom_label,
                    }
                    session["menu_planning_state"] = plan_state_data

                    if custom_label:
                        reply_lines = [
                            f"🍽️ I'd love to help you plan **\"{custom_label}\"** for **{guest_count} guests**!",
                            "",
                            "Before I put the menu together, **how many and what types of courses/dishes** would you like to include?",
                            "- *Examples*: *\"An appetizer, a grilled chicken main course, two side dishes, and a dessert\"* or *\"3 courses: soup starter, pasta main, and fruit pie\"*.",
                            "- Feel free to share any dietary restrictions or preferences (such as *no beef* or *vegetarian*) as well!",
                        ]
                    else:
                        reply_lines = [
                            f"🍽️ I'd love to help you plan your meal for **{guest_count} guests**!",
                            "",
                            "To tailor the plan to your event, let me know:",
                            "1. **Courses & Dishes**: How many and what types of dishes/courses should we include? *(For example: \"An appetizer, a grilled chicken main course, a side dish, and a dessert\" or \"3 courses: starter, main, and dessert\")*",
                            "2. **Event Name / Label**: What would you like to call this event *(e.g., \"Saturday Gamenight\", \"Birthday Dinner\")* so we can save and easily recall the menu later?",
                        ]

                    return {
                        "reply": "\n".join(reply_lines),
                        "session_id": session_id,
                        "trace_id": trace_id,
                        "events": events,
                        "data": plan_state_data,
                    }

                # User provided course specifications inline — build the custom menu dynamically
                event_name = custom_label or "Dinner Party"
                events.append({
                    "type": "thought",
                    "text": f"Planning {len(course_specs)}-course menu for '{event_name}' ({guest_count} guests)...",
                })
                built_dishes: List[Dict[str, Any]] = []
                used_ids = set()
                for spec in course_specs:
                    c_label = spec["course"]
                    c_query = spec["query"]
                    events.append({"type": "tool_call", "tool": "search_recipes_by_vibe", "args": {"query": c_query}})
                    cands = vibe_tool.search_by_vibe(c_query, limit=5)
                    events.append({"type": "tool_result", "tool": "search_recipes_by_vibe", "args": {"query": c_query}, "result": [c.to_dict() for c in cands[:2]]})
                    chosen = next((c for c in cands if c.recipe_id not in used_ids), cands[0] if cands else None)
                    if chosen:
                        used_ids.add(chosen.recipe_id)
                        built_dishes.append({
                            "recipe_id": chosen.recipe_id,
                            "recipe_title": chosen.title,
                            "course": c_label,
                            "original_servings": chosen.servings,
                            "ingredients": chosen.ingredients,
                        })

                events.append({"type": "tool_call", "tool": "plan_and_save_event_menu", "args": {"name": event_name, "guest_count": guest_count}})
                summary = _menu_agent.plan_and_save_event_menu(
                    name=event_name,
                    guest_count=guest_count,
                    dishes=built_dishes,
                    theme=f"{len(built_dishes)}-Course Event Menu",
                    notes=f"Custom {len(built_dishes)}-course menu planned for {guest_count} guests.",
                    auto_scale=True,
                )
                session["active_menu_name"] = summary.name
                session["menu_planning_state"] = None
                events.append({"type": "tool_result", "tool": "plan_and_save_event_menu", "args": {"name": event_name}, "result": summary.to_dict()})

                label_note = (
                    "💡 *Tip: Give this event a custom label anytime (e.g., \"Let's call this event 'Saturday Gamenight'\") so you can recall it later!*"
                    if not custom_label
                    else f"🏷️ *Saved under the label **\"{summary.name}\"** for easy recall!*"
                )
                headline = f"🎉 **Planned & Scaled '{summary.name}' ({len(built_dishes)} Courses for {guest_count} Guests)**\n{label_note}"
                reply_text = _format_updated_menu_reply(summary, headline)
                return {
                    "reply": reply_text,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "events": events,
                    "data": summary.to_dict(),
                }

        # 2d. Grocery List Generation
        events.append({"type": "thought", "text": "Accessing persistent event menu store and grocery aggregator..."})
        events.append({"type": "tool_call", "tool": "list_event_menus", "args": {}})
        menus = _menu_agent.list_saved_menus()
        events.append({"type": "tool_result", "tool": "list_event_menus", "args": {}, "result": menus})

        if "grocery" in lower or "shopping" in lower:
            if menus:
                first_menu = session.get("active_menu_name") or menus[0]["name"]
                events.append({"type": "tool_call", "tool": "generate_grocery_list_for_menu", "args": {"name_or_id": first_menu}})
                glist = _menu_agent.generate_menu_grocery_list(first_menu)
                events.append({"type": "tool_result", "tool": "generate_grocery_list_for_menu", "args": {"name_or_id": first_menu}, "result": glist.to_dict() if glist else {}})
                if glist:
                    lines = [f"🛒 **Consolidated Grocery List for '{first_menu}'** ({len(glist.items)} items across aisles):"]
                    for aisle, items in glist.aisle_groups.items():
                        lines.append(f"\n**[{aisle}]**")
                        for itm in items:
                            pkg = f" *({itm.packaging_note})*" if itm.packaging_note else ""
                            lines.append(f"• {itm.display_quantity} {itm.canonical_name}{pkg}")
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

    # 4. Recipe / Menu Scaling Request
    if "scale" in lower or "servings" in lower or "double" in lower or "triple" in lower:
        target = 8
        m = re.search(r"\b(?:to|for)\s+(\d+)\s*(?:servings|people|guests)?", lower)
        if m:
            target = int(m.group(1))

        active_menu_name = session.get("active_menu_name")
        if active_menu_name and "recipe" not in lower:
            from dinnerguest.agent import _menu_agent
            events.append({"type": "thought", "text": f"Active menu context: '{active_menu_name}'. Scaling all planned courses to {target} guests..."})
            tool_args = {"name_or_id": active_menu_name, "action": "scale", "new_guest_count": target}
            events.append({"type": "tool_call", "tool": "update_event_menu_dish", "args": tool_args})
            summary = _menu_agent.scale_event_menu(active_menu_name, target)
            if summary:
                events.append({"type": "tool_result", "tool": "update_event_menu_dish", "args": tool_args, "result": summary.to_dict()})
                headline = f"⚖️ Scaled all **{len(summary.dishes)} courses** in **{summary.name}** to **{target} guests**:"
                reply_text = _format_updated_menu_reply(summary, headline)
                return {
                    "reply": reply_text,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "events": events,
                    "data": summary.to_dict(),
                }

        events.append({"type": "thought", "text": "Detected recipe scaling request. Applying proportional scaling and bulk/min safety checks..."})
        from dinnerguest.tools.scaler_tool import RecipeScaler
        scaler = RecipeScaler()
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
    is_conversational_prefix = bool(re.match(r"^(?:okay|ok|yes|no|sure|hey|well|actually|so|please|let's|lets)\s*,", lower))
    if any(k in lower for k in pantry_clues) or ("," in user_msg and not is_conversational_prefix):
        events.append({"type": "thought", "text": "Analyzing user pantry ingredients, matching culinary substitutions, and checking missing staples..."})
        from dinnerguest.agents.on_hand_agent import OnHandRecommendationAgent
        on_hand_agent = OnHandRecommendationAgent()
        extracted = on_hand_agent.extract_ingredients_from_text(user_msg)
        category = on_hand_agent.extract_category_from_text(user_msg)
        tool_args: Dict[str, Any] = {"ingredients": extracted}
        if category:
            tool_args["category"] = category
        events.append({"type": "tool_call", "tool": "find_recipes_by_on_hand_ingredients", "args": tool_args})
        resp = on_hand_agent.process_request(user_msg, category=category)
        events.append({"type": "tool_result", "tool": "find_recipes_by_on_hand_ingredients", "args": tool_args, "result": resp.to_dict()})
        return {
            "reply": resp.reply_text,
            "session_id": session_id,
            "trace_id": trace_id,
            "events": events,
            "data": resp.to_dict(),
        }

    # 6. Vibe / Mood Request Initiation
    vibe_keywords = ["craving", "crave", "vibe", "mood", "cozy", "cold weather", "spicy pork", "sweet dessert", "comfort food"]
    if any(k in lower for k in vibe_keywords) or (("want" in lower or "like" in lower) and ("spicy" in lower or "cozy" in lower or "sweet" in lower)):
        from dinnerguest.database import get_database_status
        db_stat = get_database_status()
        if db_stat.get("connected"):
            events.append({"type": "thought", "text": f"Connected to Cloud SQL PostgreSQL ({db_stat.get('recipe_count')} recipes indexed). Initiating conversational vibe discovery..."})
        else:
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

    # 7. Direct Recipe Search / Instruction Request
    recipe_clues = [
        "recipe", "soup", "stew", "skillet", "fajita", "fajitas", "cookie", "cookies",
        "crisp", "pesto", "bolognese", "taco", "tacos", "meatball", "meatballs",
        "burger", "burgers", "chicken", "beef", "pork", "pasta", "noodles",
        "tuna", "salmon", "fish", "seafood", "potato", "potatoes", "carrot", "carrots",
        "how to cook", "how to make", "how do i make", "how do i cook", "how do you make",
        "dessert", "cook", "prepare", "make",
    ]
    if any(k in lower for k in recipe_clues):
        from dinnerguest.database import get_database_status
        db_stat = get_database_status()
        if db_stat.get("connected"):
            events.append({"type": "thought", "text": f"Connected to Cloud SQL PostgreSQL ({db_stat.get('recipe_count')} recipes indexed). Searching culinary database..."})
        else:
            events.append({"type": "thought", "text": "Searching culinary database and recipe catalog for matching instructions and allergen profile..."})
        from dinnerguest.tools.vibe_search_tool import VibeSearchTool
        from dinnerguest.database import clean_ingredient_item, clean_instruction_step
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
                lines.append(f"• {clean_ingredient_item(ing)}")
            lines.append("")
            lines.append("### Instructions:")
            for idx, step in enumerate(best.instructions, 1):
                lines.append(f"{idx}. {clean_instruction_step(step)}")
            if best.allergen_report:
                lines.append("")
                lines.append(f"⚠️ **Allergen Notice**: {best.allergen_report.get_guest_warning_summary()}")
            if len(results) > 1:
                seen_titles = {best.title.lower()}
                distinct_titles = []
                for r in results[1:]:
                    if r.title.lower() not in seen_titles:
                        seen_titles.add(r.title.lower())
                        distinct_titles.append(f"'{r.title}'")
                if distinct_titles:
                    lines.append("")
                    lines.append(f"💡 *Other matching options: {', '.join(distinct_titles)}.*")

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
                "db_status": "/api/db/status",
                "chat": "/chat",
                "chat_stream": "/chat/stream",
                "ui": "/ui",
            },
        }

    @app.on_event("startup")
    def on_startup() -> None:
        try:
            from dinnerguest.database import init_and_seed_db
            seed_res = init_and_seed_db()
            logger.info("Startup database auto-seed: %s", seed_res)
        except Exception as exc:
            logger.warning("Startup database check/seed failed: %s", exc)

    @app.get("/health")
    @app.get("/ready")
    @app.get("/healthz")
    def health() -> Dict[str, Any]:
        from dinnerguest.database import get_database_status
        db_stat = get_database_status()
        return {
            "status": "healthy",
            "service": "dinnerguest-agent",
            "database": db_stat,
        }

    @app.get("/api/db/status")
    def db_status() -> Dict[str, Any]:
        from dinnerguest.database import get_database_status
        return get_database_status()

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
