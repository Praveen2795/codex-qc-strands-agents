"""Helpers for loading local QC test data."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("qc_strands.data_loader")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_json_records(filename: str) -> list[dict[str, Any]]:
    """Load a list of records from a local JSON file."""
    file_path = DATA_DIR / filename
    records = json.loads(file_path.read_text(encoding="utf-8"))
    logger.info("loaded_data file=%s records=%s", file_path, len(records))
    return records


def load_population_data() -> list[dict[str, Any]]:
    """Load local population records used by the QC demo tools."""
    return _load_json_records("population.json")


def load_tag_data() -> list[dict[str, Any]]:
    """Load local account tag records used by the QC demo tools."""
    return _load_json_records("account_tags.json")


def load_arlog_data() -> list[dict[str, Any]]:
    """Load local AR log records used by the QC demo tools."""
    return _load_json_records("ar_logs.json")
