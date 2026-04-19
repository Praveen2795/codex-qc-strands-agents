"""AR log placeholder evidence tool."""

from __future__ import annotations

import logging

from strands import tool

from app.utils.data_loader import load_arlog_data

logger = logging.getLogger("qc_strands.tools.ar_logs")


DEFAULT_ARLOG_EVIDENCE = {
    "settled_in_full_found": False,
    "comment_check_performed": True,
    "comment_row_found": False,
    "latest_comment_timestamp": None,
    "latest_comment_message": None,
    "matching_settled_in_full_rows_count": 0,
    "matching_settled_in_full_rows": [],
}


@tool
def get_arlog_settlement_evidence(account_number: str) -> dict:
    """Return placeholder AR log settlement evidence for one account.

    Args:
        account_number: Account being checked for settlement evidence.
    """
    account_rows = [
        row for row in load_arlog_data() if row["account_number"] == account_number
    ]
    matching_settled_rows = [
        {
            "timestamp": row["timestamp"],
            "action_code": row.get("action_code"),
            "result_code": row.get("result_code"),
            "message": row["message"],
        }
        for row in account_rows
        if "settled in full" in (row.get("action_code") or "").lower()
        or "settled in full" in (row.get("result_code") or "").lower()
    ]

    comment_rows = [
        row for row in account_rows
        if (row.get("action_code") or "").upper() == "COMMENT"
        or (row.get("result_code") or "").upper() == "COMMENT"
    ]
    latest_comment = max(comment_rows, key=lambda r: r["timestamp"]) if comment_rows else None

    evidence = {
        "settled_in_full_found": bool(matching_settled_rows),
        "comment_check_performed": not bool(matching_settled_rows),
        "comment_row_found": bool(comment_rows) and not bool(matching_settled_rows),
        "latest_comment_timestamp": None if matching_settled_rows or latest_comment is None else latest_comment["timestamp"],
        "latest_comment_message": None if matching_settled_rows or latest_comment is None else latest_comment.get("message"),
        "matching_settled_in_full_rows_count": len(matching_settled_rows),
        "matching_settled_in_full_rows": matching_settled_rows,
    }
    if not account_rows:
        evidence = DEFAULT_ARLOG_EVIDENCE

    logger.info(
        "arlog_check account_number=%s account_rows=%s settled_in_full_found=%s matching_rows=%s comment_check_performed=%s latest_comment_timestamp=%s",
        account_number,
        len(account_rows),
        evidence["settled_in_full_found"],
        evidence["matching_settled_in_full_rows_count"],
        evidence["comment_check_performed"],
        evidence["latest_comment_timestamp"],
    )
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
