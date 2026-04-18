"""Reusable Strands orchestrator agent builder and helpers."""

from __future__ import annotations

from typing import Any

from strands import Agent

from app.config import load_prompt
from app.models.factory import build_default_agent_model


DEFAULT_ORCHESTRATOR_DESCRIPTION = (
    "Interprets a QC procedure, chooses the execution order from the procedure steps, "
    "invokes specialized sub-agents or tools, tracks progress, and aggregates downstream outputs."
)


def build_orchestrator_agent(
    data_fetcher_agent: Agent,
    qc_validation_agent: Agent,
    tools: list[Any] | None = None,
    system_prompt: str | None = None,
    model: Any | None = None,
) -> Agent:
    """Build the Phase 2 reusable orchestrator agent."""
    orchestrator_tools = tools or [
        data_fetcher_agent.as_tool(
            name="fetch_structured_qc_data",
            description="Use this agent to retrieve only the structured data required for the current QC step.",
        ),
        qc_validation_agent.as_tool(
            name="collect_qc_evidence",
            description="Use this agent to gather account-level QC evidence with the appropriate evidence tools.",
        ),
    ]
    return Agent(
        name="orchestrator_agent",
        description=DEFAULT_ORCHESTRATOR_DESCRIPTION,
        model=model or build_default_agent_model("orchestrator"),
        system_prompt=system_prompt or load_prompt("orchestrator_prompt.txt"),
        tools=orchestrator_tools,
        callback_handler=None,
    )
