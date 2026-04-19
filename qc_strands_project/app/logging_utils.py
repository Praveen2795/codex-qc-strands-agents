"""Project logging helpers for QC flow inspection."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import LOGS_DIR


def _compact_value(value: Any, *, max_length: int = 1200) -> str:
    """Return a compact string representation for logs."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True, default=str)
        except TypeError:
            text = repr(value)

    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}...<truncated>"


class AgentFileCallbackHandler:
    """Log high-signal Strands callback events for one agent."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self.logger = logging.getLogger(f"qc_strands.agent_callback.{agent_name}")
        self._response_chunks: list[str] = []

    def __call__(self, **kwargs: Any) -> None:
        """Capture tool starts and completed response text from Strands callbacks."""
        data = kwargs.get("data", "")
        complete = kwargs.get("complete", False)
        reasoning_text = kwargs.get("reasoningText")

        # current_tool_use is the official Strands kwarg; input is accumulated as streaming occurs
        current_tool_use = kwargs.get("current_tool_use") or {}
        tool_name = current_tool_use.get("name")
        if tool_name and current_tool_use.get("input") is not None:
            self.logger.info(
                "tool_start agent=%s tool=%s input=%s",
                self.agent_name,
                tool_name,
                _compact_value(current_tool_use.get("input", {})),
            )

        if reasoning_text:
            self.logger.debug(
                "reasoning_chunk agent=%s text=%s",
                self.agent_name,
                _compact_value(reasoning_text, max_length=800),
            )

        if data:
            self._response_chunks.append(data)

        if complete:
            response_text = "".join(self._response_chunks).strip()
            if response_text:
                self.logger.info(
                    "response_complete agent=%s text=%s",
                    self.agent_name,
                    _compact_value(response_text, max_length=2000),
                )
            self._response_chunks.clear()


def create_agent_callback_handler(agent_name: str) -> AgentFileCallbackHandler:
    """Create a per-agent callback handler for log-file observability."""
    return AgentFileCallbackHandler(agent_name)


# Re-export the SDK's built-in CompositeCallbackHandler so callers can import
# it from this module without knowing the SDK path.
from strands.handlers.callback_handler import CompositeCallbackHandler  # noqa: E402


def setup_project_logging(run_name: str = "demo_flow") -> Path:
    """Configure file logging for the project and return the run log path."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log_path = LOGS_DIR / f"{run_name}_{timestamp}.log"
    latest_log_path = LOGS_DIR / "latest.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(run_log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    latest_handler = logging.FileHandler(latest_log_path, mode="w", encoding="utf-8")
    latest_handler.setFormatter(formatter)
    root_logger.addHandler(latest_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)
    root_logger.addHandler(console_handler)

    strands_level_name = os.getenv("STRANDS_LOG_LEVEL", "WARNING").upper()
    strands_level = getattr(logging, strands_level_name, logging.WARNING)
    logging.getLogger("strands").setLevel(strands_level)

    # Suppress verbose AFC confirmation prints from the google-genai SDK
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("google.genai").setLevel(logging.WARNING)

    logging.getLogger("qc_strands").info(
        "logging_initialized run_log=%s latest_log=%s strands_level=%s",
        run_log_path,
        latest_log_path,
        logging.getLevelName(strands_level),
    )
    return run_log_path
