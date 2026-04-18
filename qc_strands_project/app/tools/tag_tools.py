"""Account tag placeholder evidence tool."""

from __future__ import annotations

from strands import tool


DEFAULT_SIF_EVIDENCE = {
    "sif_present": False,
    "matching_sif_rows_count": 0,
    "matching_sif_tag_dates": [],
}


SAMPLE_SIF_EVIDENCE = {
    "100001": {
        "sif_present": True,
        "matching_sif_rows_count": 2,
        "matching_sif_tag_dates": ["2026-04-10", "2026-04-16"],
    },
    "100002": {
        "sif_present": False,
        "matching_sif_rows_count": 0,
        "matching_sif_tag_dates": [],
    },
    "100003": {
        "sif_present": True,
        "matching_sif_rows_count": 1,
        "matching_sif_tag_dates": ["2026-04-12"],
    },
}


@tool
def get_account_tag_sif_presence(account_number: str) -> dict:
    """Return placeholder SIF tag evidence for one account.

    Args:
        account_number: Account being checked for SIF tag evidence.
    """
    evidence = SAMPLE_SIF_EVIDENCE.get(account_number, DEFAULT_SIF_EVIDENCE)
    return {
        "account_number": account_number,
        "check": "account_tag_sif_presence",
        "sif_present": evidence["sif_present"],
        "matching_sif_rows_count": evidence["matching_sif_rows_count"],
        "matching_sif_tag_dates": evidence["matching_sif_tag_dates"],
    }
