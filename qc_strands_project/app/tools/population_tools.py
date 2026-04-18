"""Population batch retrieval placeholder tool."""

from __future__ import annotations

from strands import tool


SAMPLE_POPULATION_ACCOUNTS = [
    {
        "account_number": "100001",
        "settlement_flag": "Y",
        "borrower": "Alex Johnson",
        "co_borrower": "Jamie Johnson",
    },
    {
        "account_number": "100002",
        "settlement_flag": "N",
        "borrower": "Taylor Smith",
        "co_borrower": "Jordan Smith",
    },
    {
        "account_number": "100003",
        "settlement_flag": "Y",
        "borrower": "Morgan Lee",
        "co_borrower": "Casey Lee",
    },
]


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
    start_index = cursor or 0
    accounts = SAMPLE_POPULATION_ACCOUNTS[start_index : start_index + batch_size]
    next_cursor = start_index + len(accounts)
    return {
        "check": "population_batch",
        "accounts": accounts,
        "next_cursor": next_cursor if next_cursor < len(SAMPLE_POPULATION_ACCOUNTS) else None,
        "has_more": next_cursor < len(SAMPLE_POPULATION_ACCOUNTS),
        "start_date": start_date,
        "end_date": end_date,
    }
