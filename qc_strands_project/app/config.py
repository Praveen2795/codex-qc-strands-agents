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
