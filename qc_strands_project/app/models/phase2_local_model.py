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


def _step_decision_deterministic(request: dict[str, Any]) -> dict[str, Any]:
    """Apply settlement QC step-level rules deterministically (phase-1 / local model).

    Mirrors the logic in qc_decision_agent._apply_step_decision_rules without
    importing from that module to avoid circular dependencies via factory.py.
    """
    account_ctx = request.get("account_context", {})
    account_number = account_ctx.get("account_number", "unknown")
    settlement_flag = str(account_ctx.get("settlement_flag", "")).upper()

    evidence_bundle = request.get("evidence_bundle", {})
    step_id = request.get("step_id", "")
    rules = request.get("evaluation_rules", [])
    rule_ids = [r["rule_id"] for r in rules if "rule_id" in r]

    evidence_items: list[dict[str, Any]] = evidence_bundle.get("evidence", [])
    tag_check = next((e for e in evidence_items if e.get("check") == "account_tag_sif_presence"), {})
    arlog_check = next((e for e in evidence_items if e.get("check") == "arlog_settlement_evidence"), {})

    sif_present: bool = bool(tag_check.get("sif_present", False))
    settled_in_full_found: bool = bool(arlog_check.get("settled_in_full_found", False))
    latest_comment: str = arlog_check.get("latest_comment_message") or ""
    comment_implies: bool = any(
        phrase in latest_comment.lower()
        for phrase in ("settled", "settlement", "paid in full", "resolved")
    )

    used_checks: list[str] = []
    if tag_check:
        used_checks.append("account_tag_sif_presence")
    if arlog_check:
        used_checks.append("arlog_settlement_evidence")

    if settlement_flag == "Y":
        rule1_outcome = "pass" if sif_present else "manual_review"
        rule23_outcome = "pass" if (settled_in_full_found or comment_implies) else "insufficient_evidence"
    elif settlement_flag == "N":
        rule1_outcome = "fail" if sif_present else "pass"
        rule23_outcome = "fail" if (settled_in_full_found or comment_implies) else "pass"
    else:
        rule1_outcome = "manual_review"
        rule23_outcome = "manual_review"

    sub_outcomes = [rule1_outcome, rule23_outcome]
    if "fail" in sub_outcomes:
        decision = "fail"
        reason = f"One or more settlement rules failed. settlement_flag={settlement_flag}; sif_present={sif_present}; settled_in_full_found={settled_in_full_found}."
    elif "insufficient_evidence" in sub_outcomes:
        decision = "insufficient_evidence"
        reason = f"Insufficient evidence to confirm settlement. settlement_flag={settlement_flag}; sif_present={sif_present}; settled_in_full_found={settled_in_full_found}."
    elif "manual_review" in sub_outcomes:
        decision = "manual_review"
        reason = f"Ambiguous evidence requires manual review. settlement_flag={settlement_flag}; sif_present={sif_present}; settled_in_full_found={settled_in_full_found}."
    else:
        decision = "pass"
        reason = f"All applicable settlement rules passed. settlement_flag={settlement_flag}; sif_present={sif_present}; settled_in_full_found={settled_in_full_found}."

    return {
        "decision_scope": "step_level",
        "step_id": step_id,
        "account_number": account_number,
        "decision": decision,
        "reason": reason,
        "used_rule_ids": rule_ids,
        "used_evidence_checks": used_checks,
    }


def _final_decision_deterministic(request: dict[str, Any]) -> dict[str, Any]:
    """Aggregate step-level decisions into a final verdict deterministically (phase-1 / local model)."""
    account_ctx = request.get("account_context", {})
    account_number = account_ctx.get("account_number", "unknown")
    step_decisions: list[dict[str, Any]] = request.get("step_decisions", [])
    final_step_id = request.get("final_step_id", "")
    rules = request.get("evaluation_rules", [])
    rule_ids = [r["rule_id"] for r in rules if "rule_id" in r]

    used_step_ids = [sd.get("step_id") for sd in step_decisions if sd.get("step_id")]
    step_outcomes = [sd.get("decision") for sd in step_decisions]

    if "fail" in step_outcomes:
        final_decision = "fail"
        reason = "One or more required step-level decisions returned fail."
    elif any(o in ("insufficient_evidence", None) for o in step_outcomes):
        final_decision = "manual_review"
        reason = "One or more required step-level decisions returned insufficient evidence or are missing."
    elif "manual_review" in step_outcomes:
        final_decision = "manual_review"
        reason = "One or more required step-level decisions require manual review."
    else:
        final_decision = "pass"
        reason = "All required step-level decisions passed."

    return {
        "decision_scope": "final_level",
        "final_step_id": final_step_id,
        "account_number": account_number,
        "decision": final_decision,
        "reason": reason,
        "used_rule_ids": rule_ids,
        "used_step_decisions": used_step_ids,
    }


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

        if self.role == "qc_decision":
            async for event in self._stream_qc_decision(messages):
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

    async def _stream_qc_decision(self, messages: Messages) -> AsyncIterable[StreamEvent]:
        """Deterministic decision model: apply settlement QC rules and emit result JSON."""
        request = _parse_json_text(_latest_user_text(messages))
        mode = request.get("decision_mode")

        if mode == "step_decision":
            result = _step_decision_deterministic(request)
        elif mode == "final_decision":
            result = _final_decision_deterministic(request)
        else:
            result = {"error": f"unknown_decision_mode: {mode}"}

        async for event in self._emit_text(json.dumps(result)):
            yield event

    async def _stream_orchestrator(self, messages: Messages) -> AsyncIterable[StreamEvent]:
        request = _parse_json_text(_latest_user_text(messages))
        procedure_document = request.get("procedure_document", {})

        # Navigate the new procedure structure
        pop_steps: list[dict[str, Any]] = procedure_document.get("population_phase", {}).get("steps", [])
        acct_steps: list[dict[str, Any]] = procedure_document.get("account_phase", {}).get("steps", [])
        evaluation_rules: list[dict[str, Any]] = procedure_document.get("evaluation_rules", [])

        # Convenience: fetch checkpoint_scope from procedure or request (runtime override)
        checkpoint_scope: dict[str, Any] = (
            request.get("checkpoint_scope")
            or procedure_document.get("checkpoint_scope", {})
        )
        batch_size: int = checkpoint_scope.get("batch_size", 2)

        tool_results = _paired_tool_results(messages)

        def _rules_for_ids(rule_ids: list[str]) -> list[dict[str, Any]]:
            return [r for r in evaluation_rules if r.get("rule_id") in rule_ids]

        # ── Pop-1: fetch population batch ─────────────────────────────────────
        if not tool_results and pop_steps:
            pop_step = pop_steps[0]
            fetch_request = {
                "step_id": pop_step["step_id"],
                "step_title": pop_step["title"],
                "requested_tools": ["get_population_batch"],
                "expected_output_type": "population_batch",
                "start_date": request.get("start_date", "2026-02-01"),
                "end_date": request.get("end_date", "2026-02-28"),
                "cursor": 0,
                "batch_size": batch_size,
            }
            async for event in self._emit_tool_use(
                tool_name="fetch_structured_qc_data",
                tool_input={"input": json.dumps(fetch_request)},
            ):
                yield event
            return

        # ── Acct-1: collect evidence for the first account ────────────────────
        if len(tool_results) == 1 and acct_steps:
            population_result = _parse_json_text(tool_results[0]["text"])
            accounts = population_result.get("accounts", [])
            selected_account = accounts[0]["account_number"] if accounts else None
            evidence_step = acct_steps[0]
            validation_request = {
                "step_id": evidence_step["step_id"],
                "step_title": evidence_step["title"],
                "account_number": selected_account,
                "requested_tools": evidence_step.get("evidence_tools", []),
                "expected_output_type": "evidence_bundle",
            }
            async for event in self._emit_tool_use(
                tool_name="collect_qc_evidence",
                tool_input={"input": json.dumps(validation_request)},
            ):
                yield event
            return

        # ── Acct-2: step-level decision ───────────────────────────────────────
        if len(tool_results) == 2 and len(acct_steps) > 1:
            population_result = _parse_json_text(tool_results[0]["text"])
            evidence_result = _parse_json_text(tool_results[1]["text"])
            accounts = population_result.get("accounts", [])
            account_data = accounts[0] if accounts else {}
            decision_step = acct_steps[1]
            step_decision_request = {
                "decision_mode": "step_decision",
                "step_id": decision_step["step_id"],
                "step_title": decision_step["title"],
                "account_context": {
                    "account_number": account_data.get("account_number"),
                    "settlement_flag": account_data.get("settlement_flag"),
                    "borrower": account_data.get("borrower"),
                },
                "evidence_bundle": evidence_result,
                "evaluation_rules": _rules_for_ids(decision_step.get("evaluation_rule_ids", [])),
            }
            async for event in self._emit_tool_use(
                tool_name="make_qc_decision",
                tool_input={"input": json.dumps(step_decision_request)},
            ):
                yield event
            return

        # ── Acct-3: final account-level decision ──────────────────────────────
        if len(tool_results) == 3 and len(acct_steps) > 2:
            population_result = _parse_json_text(tool_results[0]["text"])
            step_decision_result = _parse_json_text(tool_results[2]["text"])
            accounts = population_result.get("accounts", [])
            account_data = accounts[0] if accounts else {}
            final_step = acct_steps[2]
            final_decision_request = {
                "decision_mode": "final_decision",
                "final_step_id": final_step["step_id"],
                "account_context": {
                    "account_number": account_data.get("account_number"),
                    "settlement_flag": account_data.get("settlement_flag"),
                },
                "step_decisions": [step_decision_result],
                "evaluation_rules": _rules_for_ids(final_step.get("evaluation_rule_ids", [])),
            }
            async for event in self._emit_tool_use(
                tool_name="make_qc_decision",
                tool_input={"input": json.dumps(final_decision_request)},
            ):
                yield event
            return

        # ── All steps done: build final output ────────────────────────────────
        population_result = _parse_json_text(tool_results[0]["text"]) if tool_results else {}
        evidence_result = _parse_json_text(tool_results[1]["text"]) if len(tool_results) > 1 else {}
        step_decision_result = _parse_json_text(tool_results[2]["text"]) if len(tool_results) > 2 else {}
        final_decision_result = _parse_json_text(tool_results[3]["text"]) if len(tool_results) > 3 else {}
        current_account = evidence_result.get("account_number")

        interpreted_steps = []
        if pop_steps:
            pop_step = pop_steps[0]
            interpreted_steps.append({
                "step_id": pop_step["step_id"],
                "phase": "population_phase",
                "title": pop_step["title"],
                "preferred_agent": pop_step["preferred_agent"],
                "chosen_action": "fetch_structured_qc_data",
                "agent_request": {
                    "step_id": pop_step["step_id"],
                    "step_title": pop_step["title"],
                    "requested_tools": ["get_population_batch"],
                    "expected_output_type": "population_batch",
                    "start_date": request.get("start_date", "2026-02-01"),
                    "end_date": request.get("end_date", "2026-02-28"),
                    "cursor": 0,
                    "batch_size": batch_size,
                },
            })
        if len(acct_steps) > 0:
            evidence_step = acct_steps[0]
            interpreted_steps.append({
                "step_id": evidence_step["step_id"],
                "phase": "account_phase",
                "title": evidence_step["title"],
                "preferred_agent": evidence_step["preferred_agent"],
                "chosen_action": "collect_qc_evidence",
                "agent_request": {
                    "step_id": evidence_step["step_id"],
                    "step_title": evidence_step["title"],
                    "account_number": current_account,
                    "requested_tools": evidence_step.get("evidence_tools", []),
                    "expected_output_type": "evidence_bundle",
                },
            })
        if len(acct_steps) > 1:
            decision_step = acct_steps[1]
            interpreted_steps.append({
                "step_id": decision_step["step_id"],
                "phase": "account_phase",
                "title": decision_step["title"],
                "preferred_agent": decision_step["preferred_agent"],
                "chosen_action": "make_qc_decision",
                "agent_request": {
                    "decision_mode": "step_decision",
                    "step_id": decision_step["step_id"],
                    "evaluation_rule_ids": decision_step.get("evaluation_rule_ids", []),
                },
            })
        if len(acct_steps) > 2:
            final_step = acct_steps[2]
            interpreted_steps.append({
                "step_id": final_step["step_id"],
                "phase": "account_phase",
                "title": final_step["title"],
                "preferred_agent": final_step["preferred_agent"],
                "chosen_action": "make_qc_decision",
                "agent_request": {
                    "decision_mode": "final_decision",
                    "final_step_id": final_step["step_id"],
                    "evaluation_rule_ids": final_step.get("evaluation_rule_ids", []),
                },
            })

        outputs = []
        if pop_steps:
            outputs.append({
                "step_id": pop_steps[0]["step_id"],
                "phase": "population_phase",
                "agent_called": "fetch_structured_qc_data",
                "agent_output": population_result,
            })
        if len(acct_steps) > 0:
            outputs.append({
                "step_id": acct_steps[0]["step_id"],
                "phase": "account_phase",
                "agent_called": "collect_qc_evidence",
                "agent_output": evidence_result,
            })
        if len(acct_steps) > 1:
            outputs.append({
                "step_id": acct_steps[1]["step_id"],
                "phase": "account_phase",
                "agent_called": "make_qc_decision",
                "agent_output": step_decision_result,
            })
        if len(acct_steps) > 2:
            outputs.append({
                "step_id": acct_steps[2]["step_id"],
                "phase": "account_phase",
                "agent_called": "make_qc_decision",
                "agent_output": final_decision_result,
            })

        final_payload = {
            "task_request": request.get("task_request"),
            "procedure_name": request.get("procedure_name"),
            "batch_id": request.get("batch_id"),
            "current_account": current_account,
            "interpreted_steps": interpreted_steps,
            "outputs": outputs,
            "step_decision": step_decision_result,
            "final_decision": final_decision_result,
            "status": "completed",
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
