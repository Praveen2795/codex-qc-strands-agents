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
    """Apply settlement QC step-level rules deterministically — rule-subset-aware.

    Only evaluates the rules explicitly listed in ``evaluation_rules``.
    Mirrors the logic in qc_decision_agent._apply_step_decision_rules.
    """
    account_ctx = request.get("account_context", {})
    account_number = account_ctx.get("account_number", "unknown")
    settlement_flag = str(account_ctx.get("settlement_flag", "")).upper()

    evidence_bundle = request.get("evidence_bundle", {})
    step_id = request.get("step_id", "")
    rules: list[dict[str, Any]] = request.get("evaluation_rules", [])

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

    rule_id_set = {r["rule_id"] for r in rules if "rule_id" in r}
    used_checks: list[str] = []
    if "rule_sif_tag" in rule_id_set and tag_check:
        used_checks.append("account_tag_sif_presence")
    if {"rule_arlog_direct", "rule_arlog_comment"} & rule_id_set and arlog_check:
        used_checks.append("arlog_settlement_evidence")

    sub_outcomes: list[str] = []
    active_rule_ids: list[str] = []

    for rule in rules:
        rid = rule.get("rule_id")
        if not rid:
            continue

        if rid == "rule_sif_tag":
            if settlement_flag == "Y":
                outcome = "pass" if sif_present else "manual_review"
            elif settlement_flag == "N":
                outcome = "fail" if sif_present else "pass"
            else:
                outcome = "manual_review"
            sub_outcomes.append(outcome)
            active_rule_ids.append(rid)

        elif rid == "rule_arlog_direct":
            if settlement_flag == "Y":
                outcome = "pass" if settled_in_full_found else "insufficient_evidence"
            elif settlement_flag == "N":
                outcome = "fail" if settled_in_full_found else "pass"
            else:
                outcome = "manual_review"
            sub_outcomes.append(outcome)
            active_rule_ids.append(rid)

        elif rid == "rule_arlog_comment":
            # Conditional fallback: skip when direct AR evidence is already present
            if rule.get("rule_type") == "conditional_fallback" and settled_in_full_found:
                continue
            if settlement_flag == "Y":
                outcome = "pass" if comment_implies else "insufficient_evidence"
            elif settlement_flag == "N":
                outcome = "fail" if comment_implies else "pass"
            else:
                outcome = "manual_review"
            sub_outcomes.append(outcome)
            active_rule_ids.append(rid)

    if not sub_outcomes:
        decision = "insufficient_evidence"
        reason = "No applicable rules were evaluated for this step."
    elif "fail" in sub_outcomes:
        decision = "fail"
    elif "insufficient_evidence" in sub_outcomes:
        decision = "insufficient_evidence"
    elif "manual_review" in sub_outcomes:
        decision = "manual_review"
    else:
        decision = "pass"

    prefix_map = {
        "pass": "All applicable settlement rules passed.",
        "fail": "One or more settlement rules failed due to contradictory evidence.",
        "insufficient_evidence": "Insufficient evidence to confirm settlement status.",
        "manual_review": "Ambiguous evidence requires manual review.",
    }
    reason = (
        f"{prefix_map.get(decision, '')} Rules evaluated: {active_rule_ids}. "
        f"settlement_flag={settlement_flag}; sif_present={sif_present}; "
        f"settled_in_full_found={settled_in_full_found}; comment_implies_settlement={comment_implies}."
    )

    return {
        "decision_scope": "step_level",
        "step_id": step_id,
        "account_number": account_number,
        "decision": decision,
        "reason": reason,
        "used_rule_ids": active_rule_ids,
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

        checkpoint_scope: dict[str, Any] = (
            request.get("checkpoint_scope")
            or procedure_document.get("checkpoint_scope", {})
        )
        batch_size: int = checkpoint_scope.get("batch_size", 2)

        tool_results = _paired_tool_results(messages)

        def _rules_for_ids(rule_ids: list[str]) -> list[dict[str, Any]]:
            return [r for r in evaluation_rules if r.get("rule_id") in set(rule_ids)]

        n_pop = len(pop_steps)

        # ── Population phase ──────────────────────────────────────────────────
        if len(tool_results) < n_pop:
            pop_step = pop_steps[len(tool_results)]
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

        # Extract population data
        pop_result = _parse_json_text(tool_results[0]["text"])
        accounts = pop_result.get("accounts", [])
        account_data = accounts[0] if accounts else {}
        account_context = {
            "account_number": account_data.get("account_number"),
            "settlement_flag": account_data.get("settlement_flag"),
        }

        # ── Account phase — drive each step in procedure order ──────────────
        acct_results = tool_results[n_pop:]
        n_acct_done = len(acct_results)

        if n_acct_done < len(acct_steps):
            next_step = acct_steps[n_acct_done]
            step_type = next_step.get("step_type")
            step_id = next_step["step_id"]

            # Evidence collection step
            if step_type == "evidence_collection":
                validation_request = {
                    "step_id": step_id,
                    "step_title": next_step["title"],
                    "account_number": account_context["account_number"],
                    "requested_tools": next_step.get("evidence_tools", []),
                    "expected_output_type": "evidence_bundle",
                }
                async for event in self._emit_tool_use(
                    tool_name="collect_qc_evidence",
                    tool_input={"input": json.dumps(validation_request)},
                ):
                    yield event

            # Step decision — find the most recent preceding evidence bundle
            elif step_type == "step_decision":
                evidence_bundle: dict[str, Any] = {}
                for i in range(n_acct_done - 1, -1, -1):
                    if acct_steps[i].get("step_type") == "evidence_collection":
                        evidence_bundle = _parse_json_text(acct_results[i]["text"])
                        break
                step_decision_request = {
                    "decision_mode": "step_decision",
                    "step_id": step_id,
                    "step_title": next_step["title"],
                    "account_context": account_context,
                    "evidence_bundle": evidence_bundle,
                    "evaluation_rules": _rules_for_ids(next_step.get("evaluation_rule_ids", [])),
                }
                async for event in self._emit_tool_use(
                    tool_name="make_qc_decision",
                    tool_input={"input": json.dumps(step_decision_request)},
                ):
                    yield event

            # Final decision — aggregate all preceding step decisions
            elif step_type == "final_decision":
                step_decisions = [
                    _parse_json_text(acct_results[i]["text"])
                    for i, s in enumerate(acct_steps[:n_acct_done])
                    if s.get("step_type") == "step_decision"
                ]
                final_decision_request = {
                    "decision_mode": "final_decision",
                    "final_step_id": step_id,
                    "account_context": account_context,
                    "step_decisions": step_decisions,
                    "evaluation_rules": _rules_for_ids(next_step.get("evaluation_rule_ids", [])),
                }
                async for event in self._emit_tool_use(
                    tool_name="make_qc_decision",
                    tool_input={"input": json.dumps(final_decision_request)},
                ):
                    yield event

            return

        # ── All steps done: build final output ────────────────────────────────
        pop_result_obj = _parse_json_text(tool_results[0]["text"]) if tool_results else {}
        current_account_obj = (pop_result_obj.get("accounts") or [{}])[0]

        outputs: list[dict[str, Any]] = []
        if pop_steps:
            outputs.append({
                "step_id": pop_steps[0]["step_id"],
                "phase": "population_phase",
                "agent_called": "fetch_structured_qc_data",
                "agent_output": pop_result_obj,
            })

        step_decisions_list: list[dict[str, Any]] = []
        final_decision_obj: dict[str, Any] = {}

        for i, step in enumerate(acct_steps):
            result = _parse_json_text(acct_results[i]["text"])
            agent_called = (
                "collect_qc_evidence"
                if step["step_type"] == "evidence_collection"
                else "make_qc_decision"
            )
            outputs.append({
                "step_id": step["step_id"],
                "phase": "account_phase",
                "agent_called": agent_called,
                "agent_output": result,
            })
            if step["step_type"] == "step_decision":
                step_decisions_list.append(result)
            elif step["step_type"] == "final_decision":
                final_decision_obj = result

        last_step_decision = step_decisions_list[-1] if step_decisions_list else {}

        final_payload = {
            "task_request": request.get("task_request"),
            "procedure_name": request.get("procedure_name"),
            "batch_id": request.get("batch_id"),
            "current_account": current_account_obj,
            "interpreted_steps": [],
            "outputs": outputs,
            "step_decision": last_step_decision,
            "step_decisions": step_decisions_list,
            "final_decision": final_decision_obj,
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
