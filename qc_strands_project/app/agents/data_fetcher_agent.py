"""Reusable Strands data fetcher agent builder."""

from __future__ import annotations

from typing import Any

from strands import Agent

from app.config import load_prompt
from app.models.factory import build_default_agent_model
from app.tools.population_tools import get_population_batch


DEFAULT_DATA_FETCHER_DESCRIPTION = (
    "Retrieves structured batch data for the current QC step and returns only retrieval output "
    "without making QC judgments."
)


def build_data_fetcher_agent(
    tools: list[Any] | None = None,
    system_prompt: str | None = None,
    model: Any | None = None,
) -> Agent:
    """Build the Phase 2 reusable data fetcher agent."""
    return Agent(
        name="data_fetcher_agent",
        description=DEFAULT_DATA_FETCHER_DESCRIPTION,
        model=model or build_default_agent_model("data_fetcher"),
        system_prompt=system_prompt or load_prompt("data_fetcher_prompt.txt"),
        tools=tools or [get_population_batch],
        callback_handler=None,
    )
