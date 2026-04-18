"""Local entrypoint that proves the reusable QC workflow skeleton."""

from __future__ import annotations

import json
from pprint import pprint

from app.config import load_schema_json, normalize_agent_tool_output, parse_json_response_text
from app.agents.data_fetcher_agent import build_data_fetcher_agent
from app.agents.orchestrator_agent import (
    build_orchestrator_agent,
)
from app.agents.qc_validation_agent import build_qc_validation_agent


def demo_workflow() -> dict:
    """Demonstrate the Phase 2 agent-driven orchestration flow."""
    data_fetcher_agent = build_data_fetcher_agent()
    qc_validation_agent = build_qc_validation_agent()
    orchestrator_agent = build_orchestrator_agent(data_fetcher_agent, qc_validation_agent)
    sample_procedure = load_schema_json("sample_procedure.json")
    demo_task: dict[str, object] = {
        "qc_name": sample_procedure["qc_name"],
        "procedure_name": sample_procedure["procedure_name"],
        "batch_id": "batch-001",
        "procedure_document": sample_procedure,
    }
    demo_task["task_request"] = "Run settlement QC for February 2026"
    demo_task["start_date"] = "2026-02-01"
    demo_task["end_date"] = "2026-02-28"
    demo_task["checkpoint_scope"] = {
        "max_population_batches": 1,
        "max_accounts_to_process": 1,
        "selection_rule": "process the first account only",
        "batch_size": 2,
    }
    checkpoint_result = normalize_agent_tool_output(
        parse_json_response_text(str(orchestrator_agent(json.dumps(demo_task))))
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
