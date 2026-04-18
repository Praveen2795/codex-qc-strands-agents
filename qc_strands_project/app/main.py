"""Local entrypoint that proves the reusable QC workflow skeleton."""

from __future__ import annotations

from pprint import pprint

from app.agents.data_fetcher_agent import build_data_fetcher_agent
from app.agents.orchestrator_agent import (
    build_orchestrator_agent,
    run_checkpoint_one_workflow,
)
from app.agents.qc_validation_agent import build_qc_validation_agent
from app.schemas.response_examples import FIRST_QC_EXAMPLE


def demo_workflow() -> dict:
    """Demonstrate the reusable agent skeleton with one QC example."""
    data_fetcher_agent = build_data_fetcher_agent()
    qc_validation_agent = build_qc_validation_agent()
    orchestrator_agent = build_orchestrator_agent(data_fetcher_agent, qc_validation_agent)
    demo_task = FIRST_QC_EXAMPLE.model_dump()
    demo_task["task_request"] = "Run settlement QC for February 2026"
    demo_task["start_date"] = "2026-02-01"
    demo_task["end_date"] = "2026-02-28"
    checkpoint_result = run_checkpoint_one_workflow(
        demo_task,
        data_fetcher_agent=data_fetcher_agent,
        qc_validation_agent=qc_validation_agent,
    )

    return {
        "demo_request": demo_task["task_request"],
        "agents": {
            "orchestrator": orchestrator_agent.name,
            "data_fetcher": data_fetcher_agent.name,
            "qc_validation": qc_validation_agent.name,
        },
        "registered_tools": {
            "data_fetcher": ["get_population_batch"],
            "qc_validation": ["get_account_tag_sif_presence", "get_arlog_settlement_evidence"],
            "orchestrator": ["fetch_structured_qc_data", "collect_qc_evidence"],
        },
        "checkpoint_result": checkpoint_result,
    }


def main() -> None:
    """Run the reusable QC skeleton demonstration."""
    pprint(demo_workflow(), sort_dicts=False)


if __name__ == "__main__":
    main()
