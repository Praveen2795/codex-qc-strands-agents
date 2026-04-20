"""Population batch retrieval placeholder tool."""

from __future__ import annotations

import logging
from datetime import date

from strands import tool

from app.utils.data_loader import load_population_data

logger = logging.getLogger("qc_strands.tools.population")


def _parse_iso_date(value: str) -> date:
    """Parse a YYYY-MM-DD date string."""
    return date.fromisoformat(value)


@tool
def get_population_batch(
    start_date: str,
    end_date: str,
    cursor: int | None,
    batch_size: int,
) -> dict:
    """Return a placeholder population batch for a date range.

    Args:
        start_date: Inclusive start date for the batch query.
        end_date: Inclusive end date for the batch query.
        cursor: Starting index for pagination, or None for the first page.
        batch_size: Maximum number of accounts to return.
    """
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    population_rows = load_population_data()
    filtered_accounts = [
        {
            "account_number": row["account_number"],
            "settlement_flag": row["settlement_flag"],
            "borrower": row["borrower"],
            "co_borrower": row["co_borrower"],
        }
        for row in population_rows
        if start <= _parse_iso_date(row["as_of_date"]) <= end
    ]

    start_index = cursor or 0
    accounts = filtered_accounts[start_index : start_index + batch_size]
    next_cursor = start_index + len(accounts)
    logger.info(
        "population_batch_query start_date=%s end_date=%s cursor=%s batch_size=%s filtered=%s returned=%s next_cursor=%s has_more=%s",
        start_date,
        end_date,
        cursor,
        batch_size,
        len(filtered_accounts),
        len(accounts),
        next_cursor if next_cursor < len(filtered_accounts) else None,
        next_cursor < len(filtered_accounts),
    )
    return {
        "check": "population_batch",
        "accounts": accounts,
        "next_cursor": next_cursor if next_cursor < len(filtered_accounts) else None,
        "has_more": next_cursor < len(filtered_accounts),
        "start_date": start_date,
        "end_date": end_date,
    }
