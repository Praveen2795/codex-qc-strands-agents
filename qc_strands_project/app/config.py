"""Shared configuration helpers for the QC Strands project."""

import json
from pathlib import Path
from typing import Any

APP_NAME = "qc_strands_project"
APP_ENV = "development"
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
PROMPTS_DIR = APP_DIR / "prompts"
SCHEMAS_DIR = APP_DIR / "schemas"
LOGS_DIR = PROJECT_ROOT / "logs"


def load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


def load_schema_json(filename: str) -> dict[str, Any]:
    """Load a JSON schema or sample payload from the schemas directory."""
    return json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8"))


def parse_json_response_text(payload: str) -> dict[str, Any]:
    """Parse a model response that may include markdown code fences."""
    normalized = payload.strip()

    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        normalized = "\n".join(lines).strip()

    if normalized.startswith("json"):
        normalized = normalized[4:].strip()

    return json.loads(normalized)


def compact_procedure_for_llm(procedure: dict) -> dict:
    """Return a token-efficient subset of a procedure document for LLM context.

    Strips verbose descriptions, hints, and notes that are only useful for human
    readers. Keeps only the fields the orchestrator needs to navigate the flow:
    step IDs, types, agent roles, tool names, depends_on, and rule IDs.
    """
    def _slim_step(step: dict) -> dict:
        return {k: step[k] for k in (
            "step_id", "step_type", "title", "preferred_agent",
            "evidence_tools", "depends_on", "evaluation_rule_ids", "decision_policy",
        ) if k in step}

    def _slim_rule(rule: dict) -> dict:
        return {k: rule[k] for k in (
            "rule_id", "title", "applies_to_step", "allowed_decisions",
            "rule_type", "fallback_condition",
        ) if k in rule}

    def _slim_agent(agent: dict) -> dict:
        return {k: agent[k] for k in ("role", "tool_name") if k in agent}

    compact: dict = {
        "qc_name": procedure.get("qc_name"),
        "procedure_name": procedure.get("procedure_name"),
        "unit_of_work": procedure.get("unit_of_work"),
        "orchestration_mode": procedure.get("orchestration_mode"),
        "agents": [_slim_agent(a) for a in procedure.get("agents", [])],
        "population_phase": {
            "steps": [_slim_step(s) for s in procedure.get("population_phase", {}).get("steps", [])],
        },
        "account_phase": {
            "iteration": procedure.get("account_phase", {}).get("iteration"),
            "steps": [_slim_step(s) for s in procedure.get("account_phase", {}).get("steps", [])],
        },
        "evaluation_rules": [_slim_rule(r) for r in procedure.get("evaluation_rules", [])],
        "decision_policy": {
            k: procedure.get("decision_policy", {}).get(k)
            for k in (
                "step_decisions_enabled", "final_decision_enabled",
                "dynamic_decision_invocation", "step_aggregation_policy",
                "final_aggregation_policy", "allowed_step_outcomes", "allowed_final_outcomes",
            )
            if k in procedure.get("decision_policy", {})
        },
        "checkpoint_scope": procedure.get("checkpoint_scope", {}),
    }
    return compact


def normalize_agent_tool_output(payload: Any) -> Any:
    """Recursively unwrap Strands agent-as-tool response envelopes for demo output."""
    if isinstance(payload, list):
        return [normalize_agent_tool_output(item) for item in payload]

    if not isinstance(payload, dict):
        return payload

    if len(payload) == 1:
        key, value = next(iter(payload.items()))
        if key.endswith("_response") and isinstance(value, dict):
            output_items = value.get("output", [])
            if output_items and isinstance(output_items, list):
                first_item = output_items[0]
                if isinstance(first_item, dict) and isinstance(first_item.get("text"), str):
                    try:
                        return normalize_agent_tool_output(parse_json_response_text(first_item["text"]))
                    except json.JSONDecodeError:
                        return payload

    return {key: normalize_agent_tool_output(value) for key, value in payload.items()}
