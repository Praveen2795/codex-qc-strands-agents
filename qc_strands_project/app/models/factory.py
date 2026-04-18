"""Model factory helpers for local and Gemini-backed execution."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from app.config import PROJECT_ROOT
from app.models.phase2_local_model import PhaseTwoDeterministicModel


def build_default_agent_model(role: str) -> Any:
    """Build the default model for an agent role.

    Uses Gemini when local credentials are configured, otherwise falls back to the
    deterministic local Phase 2 model so the demo remains runnable offline.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model_name = os.getenv("GEMINI_MODEL_NAME", "").strip()

    if api_key and model_name:
        from strands.models.gemini import GeminiModel

        return GeminiModel(
            client_args={"api_key": api_key},
            model_id=model_name,
            params={
                "temperature": 0.0,
                "max_output_tokens": 2048,
            },
        )

    return PhaseTwoDeterministicModel(role)
