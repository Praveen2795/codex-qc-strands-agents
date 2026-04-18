"""Shared configuration helpers for the QC Strands project."""

from pathlib import Path

APP_NAME = "qc_strands_project"
APP_ENV = "development"
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
PROMPTS_DIR = APP_DIR / "prompts"


def load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()

