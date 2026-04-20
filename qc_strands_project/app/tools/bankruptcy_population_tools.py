"""Population batch retrieval tool for the Bankruptcy ODP Charge Off QC."""

from __future__ import annotations

import logging
from datetime import date

from strands import tool

from app.utils.data_loader import load_bankruptcy_population_data

logger = logging.getLogger("qc_strands.tools.bankruptcy_population")


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


@tool
def get_bankruptcy_population_batch(
    start_date: str,
    end_date: str,
    cursor: int | None,
    batch_size: int,
) -> dict:
    """Return a paginated batch of bankruptcy ODP accounts for a date range.

    Args:
        start_date: Inclusive start date for the batch query (YYYY-MM-DD).
        end_date: Inclusive end date for the batch query (YYYY-MM-DD).
        cursor: Starting index for pagination, or None for the first page.
        batch_size: Maximum number of accounts to return.
    """
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)

    all_rows = load_bankruptcy_population_data()
    filtered = [
        {
            "account_number": row["account_number"],
            "borrower_name": row["borrower_name"],
            "co_borrower_name": row.get("co_borrower_name"),
            "bankruptcy_chapter": row["bankruptcy_chapter"],
            "balance": row["balance"],
            "reason": row["reason"],
            "as_of_date": row["as_of_date"],
        }
        for row in all_rows
        if start <= _parse_iso_date(row["as_of_date"]) <= end
    ]

    start_index = cursor or 0
    accounts = filtered[start_index: start_index + batch_size]
    next_cursor = start_index + len(accounts)
    has_more = next_cursor < len(filtered)

    logger.info(
        "bankruptcy_population_batch start_date=%s end_date=%s cursor=%s batch_size=%s filtered=%s returned=%s next_cursor=%s has_more=%s",
        start_date,
        end_date,
        cursor,
        batch_size,
        len(filtered),
        len(accounts),
        next_cursor if has_more else None,
        has_more,
    )
    return {
        "accounts": accounts,
        "batch_size": len(accounts),
        "cursor": start_index,
        "next_cursor": next_cursor if has_more else None,
        "has_more": has_more,
        "total_filtered": len(filtered),
    }
