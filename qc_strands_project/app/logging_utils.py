"""Project logging helpers for QC flow inspection."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    HookProvider,
    HookRegistry,
)

from app.config import LOGS_DIR, parse_json_response_text

logger = logging.getLogger("qc_strands.hooks")


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


# ── Strands Hook providers ────────────────────────────────────────────────────

# Required fields that must be present in a valid sub-agent response, keyed by
# the tool name used to call that sub-agent.
_REQUIRED_FIELDS: dict[str, list[str]] = {
    "collect_qc_evidence": ["account_number", "evidence"],
    "make_qc_decision":    ["decision_scope", "decision"],
    "fetch_structured_qc_data": ["accounts"],
}


class SubAgentResponseValidationHook(HookProvider):
    """Validate and log sub-agent JSON responses after every tool call on the orchestrator.

    Fires on ``AfterToolCallEvent`` for the three orchestrator sub-agent tools:
    ``collect_qc_evidence``, ``make_qc_decision``, and ``fetch_structured_qc_data``.

    For each call:
    - Parses the returned content as JSON (stripping markdown code fences if present).
    - Checks that all required fields for that tool are present.
    - Logs warnings for malformed JSON or missing required fields (observability only).

    NOTE: ``event.retry = True`` is intentionally NOT used here. Strands agent-as-tool
    sub-agents track active requests by ID and reject duplicate invocations while still
    processing, causing "agent is already processing" failures. The orchestrator LLM is
    responsible for retrying failed sub-agent calls naturally when it receives an error.
    """

    def __init__(self) -> None:
        self._attempt_counts: dict[str, int] = {}

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AfterToolCallEvent, self.validate_and_retry)

    def validate_and_retry(self, event: AfterToolCallEvent) -> None:
        tool_name = event.tool_use.get("name", "")
        required = _REQUIRED_FIELDS.get(tool_name)
        if not required:
            return  # not a sub-agent call we validate

        tool_use_id = str(event.tool_use.get("toolUseId", tool_name))
        attempt = self._attempt_counts.get(tool_use_id, 0) + 1
        self._attempt_counts[tool_use_id] = attempt

        # SDK-level tool error — log only; the orchestrator LLM will see the error naturally
        if event.result.get("status") == "error":
            logger.warning(
                "sub_agent_tool_error tool=%s attempt=%d — sdk error, letting orchestrator handle",
                tool_name, attempt,
            )
            return

        # Parse the response content
        content_text: str = ""
        try:
            content_text = event.result["content"][0]["text"]
            parsed = parse_json_response_text(content_text)
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "sub_agent_malformed_json tool=%s attempt=%d content=%r error=%s",
                tool_name, attempt, content_text[:200], exc,
            )
            # Overwrite the result with a structured error that the orchestrator LLM can read.
            # This is cleaner than passing garbled text — the LLM gets a clear JSON error
            # instead of trying to interpret whatever non-JSON the sub-agent emitted.
            # (event.retry is intentionally NOT set: agent-as-tool retries via that path
            # cause "agent is already processing" failures with the same tool_use_id.)
            error_payload = json.dumps({
                "error": "sub_agent_malformed_response",
                "tool": tool_name,
                "detail": str(exc),
                "hint": "The sub-agent did not return valid JSON. Please retry this tool call.",
            })
            try:
                event.result["content"][0]["text"] = error_payload
            except (KeyError, IndexError, TypeError):
                pass  # content structure itself is unexpected — can't inject, log already done
            return

        # Check required fields — log only. The response is valid JSON and the
        # orchestrator LLM can still use whatever data is present. Replacing the
        # result with an error payload would discard usable data and break the flow.
        missing = [f for f in required if f not in parsed]
        if missing:
            logger.warning(
                "sub_agent_missing_fields tool=%s attempt=%d missing=%s (observability only)",
                tool_name, attempt, missing,
            )
        else:
            # Valid response — clean up tracking
            self._attempt_counts.pop(tool_use_id, None)
            logger.info(
                "sub_agent_response_valid tool=%s attempt=%d fields_ok=%s",
                tool_name, attempt, required,
            )


class ModelCallRetryHook(HookProvider):
    """Retry transient LLM API errors with exponential backoff.

    Fires on ``AfterModelCallEvent``. If ``event.exception`` is set (e.g. a
    transient ``ServiceUnavailable`` or rate-limit error from the model
    provider), sets ``event.retry = True`` and waits ``2^attempt`` seconds
    before the SDK re-invokes the model.

    Retry count is reset on each new agent invocation (``BeforeInvocationEvent``)
    so the limit applies per-call, not per agent lifetime.

    Max retries defaults to 3.
    """

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self._retry_count: int = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(AfterModelCallEvent, self._handle_retry)

    def _reset(self, event: BeforeInvocationEvent) -> None:  # noqa: ARG002
        self._retry_count = 0

    async def _handle_retry(self, event: AfterModelCallEvent) -> None:
        if event.exception is None:
            self._retry_count = 0
            return

        exc_str = str(event.exception)
        transient_signals = ("ServiceUnavailable", "rate limit", "429", "503", "timeout", "Timeout")
        is_transient = any(sig.lower() in exc_str.lower() for sig in transient_signals)

        if is_transient and self._retry_count < self.max_retries:
            self._retry_count += 1
            delay = 2 ** self._retry_count
            logger.warning(
                "model_call_transient_error attempt=%d/%d delay=%ds error=%s",
                self._retry_count, self.max_retries, delay, exc_str[:200],
            )
            await asyncio.sleep(delay)
            event.retry = True
        else:
            logger.error(
                "model_call_error attempt=%d error=%s — %s",
                self._retry_count,
                exc_str[:200],
                "max retries exhausted" if self._retry_count >= self.max_retries else "non-transient",
            )


class OrchestratorOutputRecoveryHook(HookProvider):
    """Recover from empty or non-JSON orchestrator output using AfterInvocationEvent.resume.

    When the orchestrator finishes but produces no text output (a transient LLM failure
    where the model stops without emitting content), this hook detects the empty result
    and triggers a follow-up invocation via ``event.resume`` with the full conversation
    history preserved — so no sub-agent tool calls are repeated.

    This is the Strands-recommended approach over an outer Python retry loop:
    - Preserves the conversation history (all prior tool results are still in context)
    - Lets the model produce the missing final JSON from its existing context
    - Avoids redundant sub-agent API calls

    Max resumes defaults to 2.
    """

    _RECOVERY_PROMPT = (
        "You completed all tool calls but did not emit your final JSON output. "
        "Using the tool results already in context, return the complete result JSON now. "
        "Return one JSON object only, with no markdown fences and no extra prose."
    )

    def __init__(self, max_resumes: int = 2) -> None:
        self.max_resumes = max_resumes
        self._resume_count: int = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(AfterInvocationEvent, self._recover_output)

    def _reset(self, event: BeforeInvocationEvent) -> None:  # noqa: ARG002
        self._resume_count = 0

    async def _recover_output(self, event: AfterInvocationEvent) -> None:
        if self._resume_count >= self.max_resumes:
            return
        result_text = str(event.result).strip() if event.result else ""
        if not result_text:
            self._resume_count += 1
            logger.warning(
                "orchestrator_empty_output resume=%d/%d — nudging model to emit final JSON",
                self._resume_count,
                self.max_resumes,
            )
            event.resume = self._RECOVERY_PROMPT


def setup_project_logging(run_name: str = "demo_flow") -> Path:
    """Configure file logging for the project and return the run log path.

    Each run overwrites the same fixed file (<run_name>.log) so the logs/
    folder stays clean — only one log and one JSONL per run mode.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    run_log_path = LOGS_DIR / f"{run_name}.log"

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
        "logging_initialized run_log=%s strands_level=%s",
        run_log_path,
        logging.getLevelName(strands_level),
    )
    return run_log_path
