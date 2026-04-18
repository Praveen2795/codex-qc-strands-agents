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


def load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


def load_schema_json(filename: str) -> dict[str, Any]:
    """Load a JSON schema or sample payload from the schemas directory."""
    return json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
