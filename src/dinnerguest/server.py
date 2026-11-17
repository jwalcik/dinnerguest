"""Lightweight HTTP server entrypoint for Dinnerguest cloud container deployment.

Serves health check and chat invocation endpoints, and wraps ADK root_agent.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException
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

    @app.get("/healthz")
    def healthz() -> Dict[str, str]:
        return {"status": "healthy", "service": "dinnerguest-agent"}

    @app.get("/")
    def root() -> Dict[str, Any]:
        from dinnerguest.agent import root_agent

        return {
            "service": "dinnerguest",
            "agent": getattr(root_agent, "name", "Dinnerguest"),
            "status": "ready",
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
