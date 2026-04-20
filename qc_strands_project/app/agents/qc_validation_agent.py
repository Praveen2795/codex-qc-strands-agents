"""Reusable Strands QC validation agent builder."""

from __future__ import annotations

import logging
from typing import Any

from strands import Agent

from app.config import load_prompt
from app.logging_utils import ModelCallRetryHook, ToolCallTracker, create_agent_callback_handler
from app.models.factory import build_default_agent_model
from app.tools.arlog_tools import get_arlog_settlement_evidence
from app.tools.tag_tools import get_account_tag_sif_presence

logger = logging.getLogger("qc_strands.agents.qc_validation")


DEFAULT_QC_VALIDATION_DESCRIPTION = (
    "Processes one account or work item at a time, gathers structured evidence using "
    "registered evidence tools, and returns evidence only without final QC pass/fail logic."
)


def build_qc_validation_agent(
    tools: list[Any] | None = None,
    system_prompt: str | None = None,
    model: Any | None = None,
) -> tuple[Agent, ToolCallTracker]:
    """Build the Phase 2 reusable QC validation agent.

    Returns:
        (agent, tracker) — the Strands Agent and a ToolCallTracker already
        registered on the agent's hooks.  Pass the tracker to
        ``build_orchestrator_agent(tool_tracker=tracker)`` so the
        EvidenceToolGuardHook can verify required tools were actually called.
    """
    logger.info(
        "building_agent name=qc_validation_agent tools=%s",
        [
            getattr(tool, "__name__", str(tool))
            for tool in (tools or [get_account_tag_sif_presence, get_arlog_settlement_evidence])
        ],
    )
    tracker = ToolCallTracker()
    agent = Agent(
        name="qc_validation_agent",
        description=DEFAULT_QC_VALIDATION_DESCRIPTION,
        model=model or build_default_agent_model("qc_validation"),
        system_prompt=system_prompt or load_prompt("qc_validation_prompt.txt"),
        tools=tools or [get_account_tag_sif_presence, get_arlog_settlement_evidence],
        callback_handler=create_agent_callback_handler("qc_validation_agent"),
        hooks=[ModelCallRetryHook(max_retries=3), tracker],
    )
    return agent, tracker
