"""Deterministic local models for Phase 2 agent-to-agent execution.

These models exist only to support the local checkpoint demo without requiring
external model credentials. They drive real Strands agent loops and agent-as-tool
execution, but use simple deterministic logic instead of a production LLM.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from pydantic import BaseModel

from strands.models.model import Model
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec


def _latest_user_text(messages: Messages) -> str:
    """Return the latest user text block."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        for content_block in message.get("content", []):
            if "text" in content_block:
                return content_block["text"]
    return ""


def _paired_tool_results(messages: Messages) -> list[dict[str, Any]]:
    """Pair tool results with the tool names that produced them."""
    pending_tool_names: list[str] = []
    paired_results: list[dict[str, Any]] = []

    for message in messages:
        for content_block in message.get("content", []):
            if "toolUse" in content_block:
                pending_tool_names.append(content_block["toolUse"]["name"])
                continue

            if "toolResult" not in content_block:
                continue

            tool_result = content_block["toolResult"]
            tool_name = pending_tool_names.pop(0) if pending_tool_names else "unknown_tool"
            result_text = ""
            if tool_result.get("content"):
                result_text = tool_result["content"][0].get("text", "")
            paired_results.append(
                {
                    "tool_name": tool_name,
                    "status": tool_result.get("status"),
                    "text": result_text,
                }
            )

    return paired_results


def _parse_json_text(payload: str) -> dict[str, Any]:
    """Best-effort parse for JSON text payloads."""
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


class PhaseTwoDeterministicModel(Model):
    """Deterministic local model used for the Phase 2 prototype."""

    def __init__(self, role: str) -> None:
        self.role = role
        self.config: dict[str, Any] = {"model_id": f"local-phase2-{role}"}

    def update_config(self, **model_config: Any) -> None:
        """Update the local model configuration."""
        self.config.update(model_config)

    def get_config(self) -> Any:
        """Return the current model configuration."""
        return self.config

    async def structured_output(
        self,
        output_model: type[BaseModel],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, BaseModel | Any], None]:
        """Structured output is not used in this checkpoint demo."""
        raise NotImplementedError("Structured output is not used in the Phase 2 local demo.")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        """Stream deterministic events that drive tool use and final responses."""
        tool_specs = tool_specs or []
        if self.role == "data_fetcher":
            async for event in self._stream_data_fetcher(messages):
                yield event
            return

        if self.role == "qc_validation":
            async for event in self._stream_qc_validation(messages):
                yield event
            return

        async for event in self._stream_orchestrator(messages):
            yield event

    async def _stream_data_fetcher(self, messages: Messages) -> AsyncIterable[StreamEvent]:
        request = _parse_json_text(_latest_user_text(messages))
        tool_results = _paired_tool_results(messages)

        if not tool_results:
            async for event in self._emit_tool_use(
                tool_name="get_population_batch",
                tool_input={
                    "start_date": request.get("start_date", "2026-02-01"),
                    "end_date": request.get("end_date", "2026-02-28"),
                    "cursor": request.get("cursor", 0),
                    "batch_size": request.get("batch_size", 2),
                },
            ):
                yield event
            return

        final_payload = _parse_json_text(tool_results[0]["text"])
        async for event in self._emit_text(json.dumps(final_payload)):
            yield event

    async def _stream_qc_validation(self, messages: Messages) -> AsyncIterable[StreamEvent]:
        request = _parse_json_text(_latest_user_text(messages))
        tool_results = _paired_tool_results(messages)
        requested_tools = request.get("requested_tools", [])

        if len(tool_results) < len(requested_tools):
            next_tool = requested_tools[len(tool_results)]
            async for event in self._emit_tool_use(
                tool_name=next_tool,
                tool_input={"account_number": request.get("account_number")},
            ):
                yield event
            return

        evidence_items = [_parse_json_text(result["text"]) for result in tool_results]
        final_payload = {
            "account_number": request.get("account_number"),
            "evidence_count": len(evidence_items),
            "evidence_checks": [item.get("check") for item in evidence_items],
            "evidence": evidence_items,
        }
        async for event in self._emit_text(json.dumps(final_payload)):
            yield event

    async def _stream_orchestrator(self, messages: Messages) -> AsyncIterable[StreamEvent]:
        request = _parse_json_text(_latest_user_text(messages))
        procedure_document = request.get("procedure_document", {})
        steps = procedure_document.get("steps", [])
        tool_results = _paired_tool_results(messages)

        if not tool_results and steps:
            first_step = steps[0]
            fetch_request = {
                "step_id": first_step["step_id"],
                "step_title": first_step["title"],
                "requested_tools": ["get_population_batch"],
                "expected_output_type": "population_batch",
                "start_date": request.get("start_date", "2026-02-01"),
                "end_date": request.get("end_date", "2026-02-28"),
                "cursor": 0,
                "batch_size": 2,
            }
            async for event in self._emit_tool_use(
                tool_name="fetch_structured_qc_data",
                tool_input={"input": json.dumps(fetch_request)},
            ):
                yield event
            return

        if len(tool_results) == 1 and len(steps) > 1:
            population_result = _parse_json_text(tool_results[0]["text"])
            accounts = population_result.get("accounts", [])
            selected_account = accounts[0]["account_number"] if accounts else None
            validation_step = steps[1]
            validation_request = {
                "step_id": validation_step["step_id"],
                "step_title": validation_step["title"],
                "account_number": selected_account,
                "requested_tools": validation_step.get("evidence_tools", []),
                "expected_output_type": "evidence_bundle",
            }
            async for event in self._emit_tool_use(
                tool_name="collect_qc_evidence",
                tool_input={"input": json.dumps(validation_request)},
            ):
                yield event
            return

        population_result = _parse_json_text(tool_results[0]["text"]) if tool_results else {}
        validation_result = _parse_json_text(tool_results[1]["text"]) if len(tool_results) > 1 else {}
        current_account = validation_result.get("account_number")
        interpreted_steps = []
        if steps:
            interpreted_steps.append(
                {
                    "step_id": steps[0]["step_id"],
                    "title": steps[0]["title"],
                    "preferred_agent": steps[0]["preferred_agent"],
                    "chosen_action": "call_data_fetcher_agent",
                }
            )
        if len(steps) > 1:
            interpreted_steps.append(
                {
                    "step_id": steps[1]["step_id"],
                    "title": steps[1]["title"],
                    "preferred_agent": steps[1]["preferred_agent"],
                    "chosen_action": "call_qc_validation_agent",
                }
            )

        final_payload = {
            "task_request": request.get("task_request"),
            "procedure_document": procedure_document,
            "interpreted_steps": interpreted_steps,
            "current_account": current_account,
            "outputs": [
                {
                    "step_id": steps[0]["step_id"],
                    "action": "call_data_fetcher_agent",
                    "agent_called": "data_fetcher_agent",
                    "selected_account": current_account,
                    "result": population_result,
                },
                {
                    "step_id": steps[1]["step_id"],
                    "action": "call_qc_validation_agent",
                    "agent_called": "qc_validation_agent",
                    "selected_account": current_account,
                    "result": validation_result,
                },
            ],
        }
        async for event in self._emit_text(json.dumps(final_payload)):
            yield event

    async def _emit_tool_use(self, *, tool_name: str, tool_input: dict[str, Any]) -> AsyncIterable[StreamEvent]:
        tool_use_id = f"tooluse_{tool_name}_{uuid.uuid4().hex[:8]}"
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"name": tool_name, "toolUseId": tool_use_id}},
            }
        }
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": json.dumps(tool_input)}},
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}

    async def _emit_text(self, text: str) -> AsyncIterable[StreamEvent]:
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "end_turn"}}
