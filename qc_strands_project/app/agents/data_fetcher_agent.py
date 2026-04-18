"""Reusable Strands data fetcher agent builder."""

from __future__ import annotations

import json
from typing import Any

from strands import Agent

from app.config import load_prompt
from app.tools.population_tools import get_population_batch


DEFAULT_DATA_FETCHER_DESCRIPTION = (
    "Retrieves structured batch data for the current QC step and returns only retrieval output "
    "without making QC judgments."
)

DATA_FETCH_TOOL_METHODS = {
    "get_population_batch": "get_population_batch",
}


def summarize_data_fetch_result(tool_result: dict[str, Any]) -> dict[str, Any]:
    """Return a clean summary of the active retrieval tool result."""
    accounts = tool_result.get("accounts", [])
    account_numbers = [account.get("account_number") for account in accounts]
    return {
        "check": tool_result.get("check"),
        "account_count": len(accounts),
        "account_numbers": account_numbers,
        "next_cursor": tool_result.get("next_cursor"),
        "has_more": tool_result.get("has_more"),
        "accounts": accounts,
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


def unsupported_data_fetch_tool_result(tool_name: str) -> dict[str, Any]:
    """Return a structured placeholder when a requested retrieval tool is unavailable."""
    return {
        "check": tool_name,
        "status": "unsupported_tool",
        "message": f"Data fetch tool '{tool_name}' is not registered for this agent.",
        "accounts": [],
        "next_cursor": None,
        "has_more": False,
    }


def invoke_data_fetch_tool(
    agent: Agent,
    *,
    tool_name: str,
    start_date: str,
    end_date: str,
    cursor: int | None,
    batch_size: int,
) -> dict[str, Any]:
    """Invoke one retrieval tool by registry lookup and return structured output."""
    method_name = DATA_FETCH_TOOL_METHODS.get(tool_name)
    if method_name is None:
        return unsupported_data_fetch_tool_result(tool_name)

    tool_method = getattr(agent.tool, method_name, None)
    if tool_method is None:
        return unsupported_data_fetch_tool_result(tool_name)

    tool_event = tool_method(
        start_date=start_date,
        end_date=end_date,
        cursor=cursor,
        batch_size=batch_size,
    )
    return parse_tool_event_json(tool_event)


def run_data_fetcher_agent_tool_wrapper(
    agent: Agent,
    *,
    start_date: str,
    end_date: str,
    cursor: int | None,
    batch_size: int,
) -> dict[str, Any]:
    """Run the phase-1 data fetcher agent as a thin tool-wrapper.

    This intentionally does not invoke the agent through a model-driven prompt loop yet.
    For checkpoint 1, it uses the agent's registered retrieval tool surface and returns
    a stable structured bundle for the orchestrator.
    """
    tool_result = invoke_data_fetch_tool(
        agent,
        tool_name="get_population_batch",
        start_date=start_date,
        end_date=end_date,
        cursor=cursor,
        batch_size=batch_size,
    )
    return summarize_data_fetch_result(tool_result)


def build_data_fetcher_agent(tools: list[Any] | None = None, system_prompt: str | None = None) -> Agent:
    """Build the checkpoint-1 reusable data fetcher agent."""
    return Agent(
        name="data_fetcher_agent",
        description=DEFAULT_DATA_FETCHER_DESCRIPTION,
        system_prompt=system_prompt or load_prompt("data_fetcher_prompt.txt"),
        tools=tools or [get_population_batch],
        callback_handler=None,
    )
