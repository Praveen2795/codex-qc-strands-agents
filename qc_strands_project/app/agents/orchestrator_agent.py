"""Reusable Strands orchestrator agent builder and helpers."""

from __future__ import annotations

import logging
from typing import Any

from strands import Agent

from app.config import load_prompt
from app.logging_utils import ModelCallRetryHook, SubAgentResponseValidationHook, create_agent_callback_handler
from app.models.factory import build_default_agent_model

logger = logging.getLogger("qc_strands.agents.orchestrator")


DEFAULT_ORCHESTRATOR_DESCRIPTION = (
    "Interprets a QC procedure, chooses the execution order from the procedure steps, "
    "invokes specialized sub-agents or tools, tracks progress, and aggregates downstream outputs."
)


def build_orchestrator_agent(
    data_fetcher_agent: Agent,
    qc_validation_agent: Agent,
    qc_decision_agent: Agent,
    tools: list[Any] | None = None,
    system_prompt: str | None = None,
    model: Any | None = None,
) -> Agent:
    """Build the reusable orchestrator agent.

    Registers three sub-agents as callable tools:
    - ``fetch_structured_qc_data`` — data retrieval (data_fetcher_agent)
    - ``collect_qc_evidence``      — evidence gathering (qc_validation_agent)
    - ``make_qc_decision``         — step/final decision (qc_decision_agent)
    """
    orchestrator_tools = tools or [
        data_fetcher_agent.as_tool(
            name="fetch_structured_qc_data",
            description="Use this agent to retrieve only the structured data required for the current QC step.",
        ),
        qc_validation_agent.as_tool(
            name="collect_qc_evidence",
            description="Use this agent to gather account-level QC evidence with the appropriate evidence tools.",
        ),
        qc_decision_agent.as_tool(
            name="make_qc_decision",
            description=(
                "Use this agent to evaluate collected evidence against evaluation rules and return "
                "a structured step-level or final-level QC decision. Pass decision_mode as either "
                "'step_decision' or 'final_decision'."
            ),
        ),
    ]
    logger.info(
        "building_agent name=orchestrator_agent tools=%s",
        [getattr(tool, "tool_name", getattr(tool, "__name__", str(tool))) for tool in orchestrator_tools],
    )
    return Agent(
        name="orchestrator_agent",
        description=DEFAULT_ORCHESTRATOR_DESCRIPTION,
        model=model or build_default_agent_model("orchestrator"),
        system_prompt=system_prompt or load_prompt("orchestrator_prompt.txt"),
        tools=orchestrator_tools,
        callback_handler=create_agent_callback_handler("orchestrator_agent"),
        hooks=[
            SubAgentResponseValidationHook(),
            ModelCallRetryHook(max_retries=3),
        ],
    )
