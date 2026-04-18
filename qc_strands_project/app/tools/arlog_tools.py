"""AR log placeholder evidence tool."""

from __future__ import annotations

from strands import tool


DEFAULT_ARLOG_EVIDENCE = {
    "settled_in_full_found": False,
    "comment_check_performed": True,
    "comment_row_found": False,
    "latest_comment_timestamp": None,
    "latest_comment_message": None,
    "matching_settled_in_full_rows_count": 0,
    "matching_settled_in_full_rows": [],
}


SAMPLE_ARLOG_EVIDENCE = {
    "100001": {
        "settled_in_full_found": True,
        "comment_check_performed": False,
        "comment_row_found": False,
        "latest_comment_timestamp": None,
        "latest_comment_message": None,
        "matching_settled_in_full_rows_count": 1,
        "matching_settled_in_full_rows": [
            {
                "timestamp": "2026-04-16T14:10:00Z",
                "message": "Direct AR log entry marked account settled in full.",
            },
        ],
    },
    "100002": {
        "settled_in_full_found": False,
        "comment_check_performed": True,
        "comment_row_found": False,
        "latest_comment_timestamp": None,
        "latest_comment_message": None,
        "matching_settled_in_full_rows_count": 0,
        "matching_settled_in_full_rows": [],
    },
    "100003": {
        "settled_in_full_found": False,
        "comment_check_performed": True,
        "comment_row_found": True,
        "latest_comment_timestamp": "2026-04-17T11:45:00Z",
        "latest_comment_message": "Servicing note references a settlement approval letter and account resolution.",
        "matching_settled_in_full_rows_count": 0,
        "matching_settled_in_full_rows": [],
    },
}


@tool
def get_arlog_settlement_evidence(account_number: str) -> dict:
    """Return placeholder AR log settlement evidence for one account.

    Args:
        account_number: Account being checked for settlement evidence.
    """
    evidence = SAMPLE_ARLOG_EVIDENCE.get(account_number, DEFAULT_ARLOG_EVIDENCE)
    return {
        "account_number": account_number,
        "check": "arlog_settlement_evidence",
        "settled_in_full_found": evidence["settled_in_full_found"],
        "comment_check_performed": evidence["comment_check_performed"],
        "comment_row_found": evidence["comment_row_found"],
        "latest_comment_timestamp": evidence["latest_comment_timestamp"],
        "latest_comment_message": evidence["latest_comment_message"],
        "matching_settled_in_full_rows_count": evidence["matching_settled_in_full_rows_count"],
        "matching_settled_in_full_rows": evidence["matching_settled_in_full_rows"],
    }
