"""Reusable Strands orchestrator agent builder and helpers."""

from __future__ import annotations

from typing import Any

from strands import Agent

from app.agents.data_fetcher_agent import run_data_fetcher_agent_tool_wrapper
from app.agents.qc_validation_agent import run_qc_validation_agent_tool_wrapper
from app.config import load_prompt


DEFAULT_ORCHESTRATOR_DESCRIPTION = (
    "Interprets a QC procedure, chooses the execution order from the procedure steps, "
    "invokes specialized sub-agents or tools, tracks progress, and aggregates downstream outputs."
)


def load_procedure_document(task: dict[str, Any]) -> dict[str, Any]:
    """Return a structured procedure document for orchestration."""
    return {
        "task_request": task.get("task_request"),
        "qc_name": task["qc_name"],
        "procedure_name": task["procedure_name"],
        "steps": task["procedure_steps"],
    }


def choose_next_action(step: dict[str, Any], task: dict[str, Any]) -> dict:
    """Choose the next agent call from the procedure step."""
    if step["preferred_agent"] == "data_fetcher_agent":
        return {
            "action_type": "call_data_fetcher_agent",
            "target": "data_fetcher_agent",
            "inputs": {
                "start_date": task.get("start_date", "2026-02-01"),
                "end_date": task.get("end_date", "2026-02-28"),
                "cursor": 0,
                "batch_size": 2,
            },
        }

    if step["preferred_agent"] == "qc_validation_agent":
        return {
            "action_type": "call_qc_validation_agent",
            "target": "qc_validation_agent",
            "inputs": {
                "evidence_tools": step.get("evidence_tools", []),
            },
        }

    return {
        "action_type": "unsupported",
        "target": step["preferred_agent"],
        "inputs": {},
    }


def run_checkpoint_one_workflow(
    task: dict[str, Any],
    *,
    data_fetcher_agent: Agent,
    qc_validation_agent: Agent,
) -> dict[str, Any]:
    """Execute a procedure-driven checkpoint-1 workflow.

    This implementation is intentionally limited to:
    - fetching one population batch
    - processing only the first account from that batch
    - executing a few steps end-to-end based on the procedure
    """
    procedure_document = load_procedure_document(task)
    step_outputs: list[dict[str, Any]] = []
    selected_account: str | None = None
    interpreted_steps: list[dict[str, Any]] = []

    for step in procedure_document["steps"]:
        action = choose_next_action(step, task)
        interpreted_steps.append(
            {
                "step_id": step["step_id"],
                "title": step["title"],
                "preferred_agent": step["preferred_agent"],
                "chosen_action": action["action_type"],
                "action_inputs": action["inputs"],
            }
        )

        if action["action_type"] == "call_data_fetcher_agent":
            population_context = run_data_fetcher_agent_tool_wrapper(data_fetcher_agent, **action["inputs"])
            selected_account = (
                population_context["accounts"][0]["account_number"] if population_context["accounts"] else None
            )
            step_outputs.append(
                {
                    "step_id": step["step_id"],
                    "action": action["action_type"],
                    "agent_called": "data_fetcher_agent",
                    "selected_account": selected_account,
                    "result": population_context,
                }
            )
            continue

        if action["action_type"] == "call_qc_validation_agent" and selected_account:
            evidence_bundle = run_qc_validation_agent_tool_wrapper(
                qc_validation_agent,
                account_number=selected_account,
                evidence_tools=action["inputs"]["evidence_tools"],
            )
            step_outputs.append(
                {
                    "step_id": step["step_id"],
                    "action": action["action_type"],
                    "agent_called": "qc_validation_agent",
                    "selected_account": selected_account,
                    "result": evidence_bundle,
                }
            )
            continue

    return {
        "task_request": task.get("task_request"),
        "procedure_document": procedure_document,
        "interpreted_steps": interpreted_steps,
        "current_account": selected_account,
        "outputs": step_outputs,
    }


def build_orchestrator_agent(
    data_fetcher_agent: Agent,
    qc_validation_agent: Agent,
    tools: list[Any] | None = None,
    system_prompt: str | None = None,
) -> Agent:
    """Build a reusable orchestrator agent for QC workflows."""
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
        system_prompt=system_prompt or load_prompt("orchestrator_prompt.txt"),
        tools=orchestrator_tools,
        callback_handler=None,
    )
