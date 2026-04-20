"""Evidence and computation tools for the Bankruptcy ODP Charge Off QC."""

from __future__ import annotations

import logging
from datetime import date

from strands import tool

from app.utils.data_loader import (
    load_chargeoff_status_data,
    load_bankruptcy_chargeoff_data,
    load_bankruptcy_tags_data,
)

logger = logging.getLogger("qc_strands.tools.bankruptcy_odp")

_EXPECTED_CHARGEOFF_TAG = "CHARGE OFF"
_EXPECTED_BANKRUPTCY_TAG = "Confirmed BK via Scrub"


@tool
def get_chargeoff_tag_evidence(account_number: str) -> dict:
    """Return charge-off status evidence for one account.

    Checks whether the expected 'CHARGE OFF' status code is present in the
    account's status codes. Returns structured evidence only — no pass/fail.

    Args:
        account_number: Account being checked for charge-off status.
    """
    records = [
        row for row in load_chargeoff_status_data()
        if row["account_number"] == account_number
    ]

    if not records:
        logger.info(
            "chargeoff_tag_check account_number=%s record_found=False expected_tag_present=False",
            account_number,
        )
        return {
            "account_number": account_number,
            "check": "chargeoff_tag_evidence",
            "expected_tag_present": False,
            "expected_tag_value": _EXPECTED_CHARGEOFF_TAG,
            "matching_tags": [],
        }

    if len(records) > 1:
        logger.warning(
            "chargeoff_tag_check account_number=%s multiple_records_found=%s using_first",
            account_number,
            len(records),
        )

    status_codes = records[0].get("status_codes", [])
    matching = [code for code in status_codes if code == _EXPECTED_CHARGEOFF_TAG]
    expected_tag_present = bool(matching)

    logger.info(
        "chargeoff_tag_check account_number=%s record_found=True expected_tag_present=%s matching_tags=%s",
        account_number,
        expected_tag_present,
        matching,
    )
    return {
        "account_number": account_number,
        "check": "chargeoff_tag_evidence",
        "expected_tag_present": expected_tag_present,
        "expected_tag_value": _EXPECTED_CHARGEOFF_TAG,
        "matching_tags": matching,
    }


@tool
def get_bankruptcy_notification_and_chargeoff_dates(account_number: str) -> dict:
    """Return bankruptcy notification date and charge-off date for one account.

    Returns both dates as raw strings for downstream SLA computation.
    Does not compute date differences — that is handled by a separate tool.

    Args:
        account_number: Account being checked for timing evidence.
    """
    records = [
        row for row in load_bankruptcy_chargeoff_data()
        if row["account_number"] == account_number
    ]

    if not records:
        logger.info(
            "bankruptcy_chargeoff_dates_check account_number=%s record_found=False",
            account_number,
        )
        return {
            "account_number": account_number,
            "check": "bankruptcy_chargeoff_dates_evidence",
            "bankruptcy_notification_date": None,
            "charge_off_date": None,
        }

    if len(records) > 1:
        logger.warning(
            "bankruptcy_chargeoff_dates_check account_number=%s multiple_records_found=%s using_first",
            account_number,
            len(records),
        )

    record = records[0]
    bk_date = record.get("bankruptcy_notification_date")
    co_date = record.get("charge_off_notification_date")

    logger.info(
        "bankruptcy_chargeoff_dates_check account_number=%s record_found=True bankruptcy_notification_date=%s charge_off_date=%s",
        account_number,
        bk_date,
        co_date,
    )
    return {
        "account_number": account_number,
        "check": "bankruptcy_chargeoff_dates_evidence",
        "bankruptcy_notification_date": bk_date,
        "charge_off_date": co_date,
    }


@tool
def calculate_days_between_dates(
    start_date: str | None,
    end_date: str | None,
) -> dict:
    """Compute the number of calendar days between two ISO date strings.

    Pure computation — no data retrieval, no pass/fail decisions.
    Returns null for days_difference when either input is missing or unparseable.

    Args:
        start_date: The earlier date (e.g. bankruptcy_notification_date). ISO format YYYY-MM-DD or null.
        end_date: The later date (e.g. charge_off_date). ISO format YYYY-MM-DD or null.
    """
    if not start_date:
        logger.info("date_diff_calculation start_date=None calculation_successful=False")
        return {
            "check": "date_difference_calculation",
            "start_date": start_date,
            "end_date": end_date,
            "days_difference": None,
            "calculation_successful": False,
            "reason": "start_date is missing",
        }

    if not end_date:
        logger.info("date_diff_calculation end_date=None calculation_successful=False")
        return {
            "check": "date_difference_calculation",
            "start_date": start_date,
            "end_date": end_date,
            "days_difference": None,
            "calculation_successful": False,
            "reason": "end_date is missing",
        }

    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)
    except ValueError as exc:
        logger.info("date_diff_calculation parse_error=%s calculation_successful=False", exc)
        return {
            "check": "date_difference_calculation",
            "start_date": start_date,
            "end_date": end_date,
            "days_difference": None,
            "calculation_successful": False,
            "reason": f"date parse error: {exc}",
        }

    days = (parsed_end - parsed_start).days
    logger.info(
        "date_diff_calculation start_date=%s end_date=%s days_difference=%s calculation_successful=True",
        start_date,
        end_date,
        days,
    )
    return {
        "check": "date_difference_calculation",
        "start_date": start_date,
        "end_date": end_date,
        "days_difference": days,
        "calculation_successful": True,
        "reason": None,
    }


@tool
def get_bankruptcy_tag_evidence(account_number: str) -> dict:
    """Return bankruptcy tag evidence for one account.

    Checks whether the expected 'Confirmed BK via Scrub' tag is present.
    Returns structured evidence only — no pass/fail.

    Args:
        account_number: Account being checked for bankruptcy tag evidence.
    """
    records = [
        row for row in load_bankruptcy_tags_data()
        if row["account_number"] == account_number
    ]

    if not records:
        logger.info(
            "bankruptcy_tag_check account_number=%s record_found=False expected_tag_present=False",
            account_number,
        )
        return {
            "account_number": account_number,
            "check": "bankruptcy_tag_evidence",
            "expected_tag_present": False,
            "expected_tag_value": _EXPECTED_BANKRUPTCY_TAG,
            "matching_tags": [],
        }

    if len(records) > 1:
        logger.warning(
            "bankruptcy_tag_check account_number=%s multiple_records_found=%s using_first",
            account_number,
            len(records),
        )

    tags = records[0].get("tags", [])
    matching = [tag for tag in tags if tag == _EXPECTED_BANKRUPTCY_TAG]
    expected_tag_present = bool(matching)

    logger.info(
        "bankruptcy_tag_check account_number=%s record_found=True expected_tag_present=%s matching_tags=%s",
        account_number,
        expected_tag_present,
        matching,
    )
    return {
        "account_number": account_number,
        "check": "bankruptcy_tag_evidence",
        "expected_tag_present": expected_tag_present,
        "expected_tag_value": _EXPECTED_BANKRUPTCY_TAG,
        "matching_tags": matching,
    }
