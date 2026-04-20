"""
Hallucination-trigger test for the EvidenceToolGuardHook.

This script is TEMPORARY — for demonstrating that the guard catches a missing
tool call. It does NOT modify any production files.

What it does:
  - Loads the UNCHANGED real Bankruptcy ODP QC procedure JSON (no fake tools injected)
  - Builds qc_validation_agent with get_chargeoff_tag_evidence REMOVED from the
    registered tool list → simulates the LLM deciding to skip that required tool
  - The orchestrator calls collect_qc_evidence with requested_tools=['get_chargeoff_tag_evidence']
    (as the normal procedure demands)
  - The qc_validation_agent cannot call get_chargeoff_tag_evidence (not registered) so
    EvidenceToolGuardHook fires: evidence_tool_guard_FAIL
  - The orchestrator receives a structured error JSON instead of hallucinated evidence

This is the same protection that fires when the LLM hallucinates evidence without
calling the required tool — from the guard's perspective, "tool not called" looks
identical whether the tool was never registered or was registered but the LLM skipped it.

Expected output:
  - Log line:  evidence_tool_guard_FAIL missing=['get_chargeoff_tag_evidence'] ...
  - Guard injects error JSON into collect_qc_evidence result

Run with:
  PYTHONPATH=/path/to/qc_strands_project .venv/bin/python3 test_guard_hallucination.py

Or from the project root:
  PYTHONPATH=$(pwd) ../.venv/bin/python3 test_guard_hallucination.py
"""

from __future__ import annotations

import json
import logging

from app.agents.data_fetcher_agent import build_data_fetcher_agent
from app.agents.orchestrator_agent import build_orchestrator_agent
from app.agents.qc_decision_agent import build_qc_decision_agent
from app.agents.qc_validation_agent import build_qc_validation_agent
from app.config import load_schema_json
from app.logging_utils import setup_project_logging
from app.tools.bankruptcy_population_tools import get_bankruptcy_population_batch
from app.tools.bankruptcy_odp_tools import (
    get_chargeoff_tag_evidence,
    get_bankruptcy_notification_and_chargeoff_dates,
    calculate_days_between_dates,
    get_bankruptcy_tag_evidence,
)

logger = logging.getLogger("qc_strands.test_guard")

# ─────────────────────────────────────────────────────────────────────────────
# ANSI helpers for readable output
# ─────────────────────────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_RED    = "\033[31m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"

def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET


def main() -> None:
    run_log_path = setup_project_logging("test_guard_hallucination")
    print()
    print(_c("━" * 72, _CYAN, _BOLD))
    print(_c("  HALLUCINATION GUARD TEST", _CYAN, _BOLD))
    print(_c("━" * 72, _CYAN, _BOLD))
    print()
    print("  Strategy: keep procedure UNCHANGED; remove a required tool from")
    print("  qc_validation_agent's registered list.")
    print()
    print("  Procedure step acct-1a requires: " +
          _c("get_chargeoff_tag_evidence", _YELLOW, _BOLD))
    print("  That tool is " + _c("NOT registered", _RED, _BOLD) +
          " on qc_validation_agent → simulates LLM skipping it.")
    print("  Guard should fire: " + _c("evidence_tool_guard_FAIL", _RED, _BOLD))
    print()

    # ── 1. Load the real (unmodified) procedure ───────────────────────────────
    procedure = load_schema_json("bankruptcy_odp_chargeoff_procedure.json")

    print()
    print(_c("  ── AGENT EXECUTION STARTS ──────────────────────────────────────", "\033[2m"))
    print()

    # ── 2. Build agents — get_chargeoff_tag_evidence deliberately withheld ────
    data_fetcher_agent = build_data_fetcher_agent(tools=[get_bankruptcy_population_batch])
    qc_validation_agent, _tool_tracker = build_qc_validation_agent(tools=[
        # get_chargeoff_tag_evidence intentionally excluded — simulates LLM skipping it
        get_bankruptcy_notification_and_chargeoff_dates,
        calculate_days_between_dates,
        get_bankruptcy_tag_evidence,
    ])
    qc_decision_agent = build_qc_decision_agent()
    orchestrator_agent = build_orchestrator_agent(
        data_fetcher_agent,
        qc_validation_agent,
        qc_decision_agent,
        tool_tracker=_tool_tracker,  # guard wired in
    )

    # ── 3. Run with the unmodified procedure ─────────────────────────────────
    demo_task = {
        "qc_name": procedure["qc_name"],
        "procedure_document": procedure,            # ← unchanged real procedure
        "task_request": "Run Bankruptcy ODP Charge Off QC for January 2026 [GUARD TEST]",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "cursor": 0,
    }

    try:
        result_str = str(orchestrator_agent(json.dumps(demo_task)))
    except Exception as exc:
        result_str = f"EXCEPTION: {exc}"

    # ── 4. Print guard verdict from log ──────────────────────────────────────
    print()
    print(_c("━" * 72, _CYAN, _BOLD))
    print(_c("  GUARD TEST RESULT", _CYAN, _BOLD))
    print(_c("━" * 72, _CYAN, _BOLD))
    print()

    log_path = run_log_path
    guard_lines = []
    try:
        with open(log_path) as f:
            for line in f:
                if "evidence_tool_guard" in line:
                    guard_lines.append(line.strip())
    except FileNotFoundError:
        pass

    if any("evidence_tool_guard_FAIL" in l for l in guard_lines):
        print(_c("  ✓ GUARD FIRED — evidence_tool_guard_FAIL detected", _GREEN, _BOLD))
        print()
        for l in guard_lines:
            tag = "FAIL" if "FAIL" in l else "OK"
            colour = _RED if tag == "FAIL" else _GREEN
            print(f"    {_c(tag, colour, _BOLD)}: {l.split('| ')[-1]}")
    elif any("evidence_tool_guard_OK" in l for l in guard_lines):
        print(_c("  ✗ GUARD DID NOT FIRE — LLM somehow called the unregistered tool (unexpected)", _RED, _BOLD))
        for l in guard_lines:
            print(f"    {l.split('| ')[-1]}")
    else:
        print(_c("  ? No guard events found in log — run may have failed before evidence step", _YELLOW, _BOLD))
        print(f"    Check: {log_path}")

    print()
    print(f"  Full log: {log_path}")
    print()


if __name__ == "__main__":
    main()
