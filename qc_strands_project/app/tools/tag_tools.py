"""Account tag placeholder evidence tool."""

from __future__ import annotations

import logging

from strands import tool

from app.utils.data_loader import load_tag_data

logger = logging.getLogger("qc_strands.tools.tags")


DEFAULT_SIF_EVIDENCE = {
    "sif_present": False,
    "matching_sif_rows_count": 0,
    "matching_sif_tag_dates": [],
}


@tool
def get_account_tag_sif_presence(account_number: str) -> dict:
    """Return placeholder SIF tag evidence for one account.

    Args:
        account_number: Account being checked for SIF tag evidence.
    """
    matching_rows = [
        row
        for row in load_tag_data()
        if row["account_number"] == account_number and row["tag_type"] == "SIF"
    ]
    matching_dates = sorted(
        row["tag_date"]
        for row in matching_rows
        if row.get("tag_date")
    )
    evidence = {
        "sif_present": bool(matching_rows),
        "matching_sif_rows_count": len(matching_rows),
        "matching_sif_tag_dates": matching_dates,
    }
    if not matching_rows:
        evidence = DEFAULT_SIF_EVIDENCE

    logger.info(
        "tag_check account_number=%s sif_present=%s matching_sif_rows_count=%s matching_sif_tag_dates=%s",
        account_number,
        evidence["sif_present"],
        evidence["matching_sif_rows_count"],
        evidence["matching_sif_tag_dates"],
    )
    return {
        "account_number": account_number,
        "check": "account_tag_sif_presence",
        "sif_present": evidence["sif_present"],
        "matching_sif_rows_count": evidence["matching_sif_rows_count"],
        "matching_sif_tag_dates": evidence["matching_sif_tag_dates"],
    }
