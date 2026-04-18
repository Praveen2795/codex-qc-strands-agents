"""Reusable Strands QC validation agent builder."""

from __future__ import annotations

import json
from typing import Any

from strands import Agent

from app.config import load_prompt
from app.tools.arlog_tools import get_arlog_settlement_evidence
from app.tools.tag_tools import get_account_tag_sif_presence


DEFAULT_QC_VALIDATION_DESCRIPTION = (
    "Processes one account or work item at a time, gathers structured evidence using "
    "registered evidence tools, and returns evidence only without final QC pass/fail logic."
)

VALIDATION_TOOL_METHODS = {
    "get_account_tag_sif_presence": "get_account_tag_sif_presence",
    "get_arlog_settlement_evidence": "get_arlog_settlement_evidence",
}


def summarize_validation_evidence(account_number: str, evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a clean account-level evidence bundle."""
    return {
        "account_number": account_number,
        "evidence_count": len(evidence_items),
        "evidence_checks": [item.get("check") for item in evidence_items],
        "evidence": evidence_items,
    }


def parse_tool_event_json(tool_event: dict[str, Any]) -> dict[str, Any]:
    """Extract the JSON payload from a Strands direct tool-call event."""
    content = tool_event.get("content", [])
    if not content:
        return {"check": "tool_event_parse_error", "status": "missing_content", "raw_event": tool_event}

    payload_text = content[0].get("text")
    if payload_text is None:
        return {"check": "tool_event_parse_error", "status": "missing_text", "raw_event": tool_event}

    try:
        return json.loads(payload_text)
    except json.JSONDecodeError:
        return {
            "check": "tool_event_parse_error",
            "status": "invalid_json",
            "raw_text": payload_text,
        }


def unsupported_validation_tool_result(account_number: str, tool_name: str) -> dict[str, Any]:
    """Return a structured placeholder when a requested evidence tool is unavailable."""
    return {
        "account_number": account_number,
        "check": tool_name,
        "status": "unsupported_tool",
        "message": f"Validation tool '{tool_name}' is not registered for this agent.",
    }


def invoke_validation_tool(agent: Agent, *, account_number: str, tool_name: str) -> dict[str, Any]:
    """Invoke one validation tool by registry lookup and return structured output."""
    method_name = VALIDATION_TOOL_METHODS.get(tool_name)
    if method_name is None:
        return unsupported_validation_tool_result(account_number, tool_name)

    tool_method = getattr(agent.tool, method_name, None)
    if tool_method is None:
        return unsupported_validation_tool_result(account_number, tool_name)

    tool_event = tool_method(account_number=account_number)
    return parse_tool_event_json(tool_event)


def run_qc_validation_agent_tool_wrapper(
    agent: Agent,
    *,
    account_number: str,
    evidence_tools: list[str],
) -> dict[str, Any]:
    """Run the phase-1 validation agent as a thin tool-wrapper for one account.

    This intentionally does not invoke the agent through a model-driven prompt loop yet.
    For checkpoint 1, it uses the agent's registered tool surface to retrieve evidence
    and returns a stable structured bundle for the orchestrator.
    """
    evidence_items = [
        invoke_validation_tool(
            agent,
            account_number=account_number,
            tool_name=tool_name,
        )
        for tool_name in evidence_tools
    ]

    return summarize_validation_evidence(
        account_number=account_number,
        evidence_items=evidence_items,
    )


def build_qc_validation_agent(tools: list[Any] | None = None, system_prompt: str | None = None) -> Agent:
    """Build a reusable evidence collection agent for QC workflows."""
    return Agent(
        name="qc_validation_agent",
        description=DEFAULT_QC_VALIDATION_DESCRIPTION,
        system_prompt=system_prompt or load_prompt("qc_validation_prompt.txt"),
        tools=tools or [get_account_tag_sif_presence, get_arlog_settlement_evidence],
        callback_handler=None,
    )
