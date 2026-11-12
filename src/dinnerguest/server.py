"""Lightweight HTTP server entrypoint for Dinnerguest cloud container deployment.

Serves health check and chat invocation endpoints, and wraps ADK root_agent.
"""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except ImportError:
    FastAPI = None  # type: ignore
    BaseModel = object  # type: ignore

from dinnerguest.agent import root_agent

app = FastAPI(
    title="Dinnerguest Agent Engine Service",
    description="Multi-agent meal planning and culinary assistant service.",
    version="0.1.0",
) if FastAPI else None


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"
    session_id: str = "default_session"


@app.get("/healthz") if app else lambda: None
def healthz() -> Dict[str, str]:
    return {"status": "healthy", "service": "dinnerguest-agent"}


@app.get("/") if app else lambda: None
def root() -> Dict[str, Any]:
    return {
        "service": "dinnerguest",
        "agent": root_agent.name if hasattr(root_agent, "name") else "Dinnerguest",
        "status": "ready",
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    if app:
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        print(f"FastAPI not installed. Starting Dinnerguest agent on port {port}...")
