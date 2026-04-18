"""Reusable Strands QC decision agent builder and phase-1 helpers.

PHASE-1 / CHECKPOINT NOTE:
The `run_qc_decision_agent_wrapper` function and the deterministic rule helpers
in this module are for checkpoint/local flow validation only. They apply
settlement QC rules directly without invoking the full Strands agent loop.

In production, the decision agent is called via `agent.as_tool()` from the
orchestrator, and the actual LLM reasons over the prompt, context, evidence,
and rules to produce each decision.
"""

from __future__ import annotations

import logging
from typing import Any

from strands import Agent

from app.config import load_prompt
from app.logging_utils import create_agent_callback_handler
from app.models.factory import build_default_agent_model

logger = logging.getLogger("qc_strands.agents.qc_decision")

VALID_STEP_DECISIONS = frozenset({"pass", "fail", "insufficient_evidence", "manual_review"})
VALID_FINAL_DECISIONS = frozenset({"pass", "fail", "manual_review"})

DEFAULT_QC_DECISION_DESCRIPTION = (
    "Evaluates structured evidence and evaluation rules for one account or one decision "
    "step at a time. Returns step-level pass/fail decisions and final account-level QC "
    "verdicts. Does not retrieve raw data or call evidence tools."
)


# ──────────────────────────────────────────────────────────────────────────────
# Agent builder
# ──────────────────────────────────────────────────────────────────────────────

def build_qc_decision_agent(
    tools: list[Any] | None = None,
    system_prompt: str | None = None,
    model: Any | None = None,
) -> Agent:
    """Build the reusable QC decision agent.

    The decision agent has no data-retrieval tools. It reasons only over the
    account context, evidence bundle, and evaluation rules supplied to it.
    """
    effective_tools = tools or []
    logger.info(
        "building_agent name=qc_decision_agent tools=%s",
        [getattr(t, "__name__", str(t)) for t in effective_tools] if effective_tools else "(none - reasoning only)",
    )
    return Agent(
        name="qc_decision_agent",
        description=DEFAULT_QC_DECISION_DESCRIPTION,
        model=model or build_default_agent_model("qc_decision"),
        system_prompt=system_prompt or load_prompt("qc_decision_prompt.txt"),
        tools=effective_tools,
        callback_handler=create_agent_callback_handler("qc_decision_agent"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Phase-1 runtime wrapper
# ──────────────────────────────────────────────────────────────────────────────

def run_qc_decision_agent_wrapper(
    decision_request: dict[str, Any],
    agent: Agent | None = None,  # noqa: ARG001 — reserved for production path
) -> dict[str, Any]:
    """Apply settlement QC decision rules to a single decision request.

    PHASE-1 / CHECKPOINT NOTE:
    This wrapper is intended for checkpoint/local flow validation only.
    It applies deterministic settlement QC rules directly, bypassing the
    full Strands multi-agent execution loop.

    In the production execution model, the decision agent is invoked by the
    orchestrator via `agent.as_tool("make_qc_decision", ...)` and the LLM
    reasons over the prompt, evidence, and rules to produce each decision.
    The `agent` parameter is accepted here but not yet used; it is reserved
    for a future phase that routes complex cases through the full agent loop.

    Args:
        decision_request: A dict with `decision_mode` set to either
            ``"step_decision"`` or ``"final_decision"``, plus the required
            fields for that mode.
        agent: Reserved for production path. Not used in phase-1.

    Returns:
        A normalized decision dict matching the step-level or final-level
        output schema.
    """
    mode = decision_request.get("decision_mode")
    step_ref = decision_request.get("step_id") or decision_request.get("final_step_id")
    account_number = decision_request.get("account_context", {}).get("account_number")

    logger.info(
        "decision_request_start decision_mode=%s step_id=%s account_number=%s",
        mode,
        step_ref,
        account_number,
    )

    validation_error = validate_decision_input(decision_request)
    if validation_error:
        logger.warning("decision_input_invalid reason=%s", validation_error)
        return {
            "decision_scope": "error",
            "error": validation_error,
        }

    if mode == "step_decision":
        raw = _apply_step_decision_rules(decision_request)
        result = summarize_step_decision(raw)
    else:
        raw = _apply_final_decision_rules(decision_request)
        result = summarize_final_decision(raw)

    logger.info(
        "decision_result decision_scope=%s account_number=%s decision=%s used_rule_ids=%s",
        result.get("decision_scope"),
        result.get("account_number"),
        result.get("decision"),
        result.get("used_rule_ids"),
    )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Input validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_decision_input(request: dict[str, Any]) -> str | None:
    """Return an error message if the decision request is structurally invalid.

    Returns ``None`` when the request is valid.
    """
    mode = request.get("decision_mode")
    if mode not in ("step_decision", "final_decision"):
        return f"Unknown decision_mode: {mode!r}. Expected 'step_decision' or 'final_decision'."

    if not isinstance(request.get("account_context"), dict):
        return "Missing or invalid 'account_context'."

    if not request["account_context"].get("account_number"):
        return "Missing 'account_number' in account_context."

    if mode == "step_decision":
        if not request.get("step_id"):
            return "Missing 'step_id' for step_decision mode."
        if not isinstance(request.get("evidence_bundle"), dict):
            return "Missing or invalid 'evidence_bundle' for step_decision mode."
        if not isinstance(request.get("evaluation_rules"), list):
            return "Missing or invalid 'evaluation_rules' for step_decision mode."

    if mode == "final_decision":
        if not request.get("final_step_id"):
            return "Missing 'final_step_id' for final_decision mode."
        if not isinstance(request.get("step_decisions"), list):
            return "Missing or invalid 'step_decisions' for final_decision mode."
        if not isinstance(request.get("evaluation_rules"), list):
            return "Missing or invalid 'evaluation_rules' for final_decision mode."

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Settlement QC rule logic  (checkpoint-1, deterministic)
# ──────────────────────────────────────────────────────────────────────────────

def _apply_step_decision_rules(request: dict[str, Any]) -> dict[str, Any]:
    """Apply settlement QC step-level rules against an evidence bundle.

    Implements the agreed settlement QC decision matrix:

    Rule 1 — account_tag SIF presence vs settlement_flag:
        flag=Y + sif_present=true  → pass
        flag=Y + sif_present=false → manual_review
        flag=N + sif_present=true  → fail
        flag=N + sif_present=false → pass

    Rule 2/3 — AR log evidence vs settlement_flag:
        flag=Y + settled_in_full_found=true         → pass
        flag=Y + comment implies settlement          → pass
        flag=Y + neither                             → insufficient_evidence
        flag=N + settled_in_full_found=true          → fail
        flag=N + comment implies settlement           → fail
        flag=N + neither                             → pass

    Step outcome aggregation (any_fail_fails policy):
        any fail              → step decision = fail
        any insufficient_evidence → step decision = insufficient_evidence
        any manual_review     → step decision = manual_review
        all pass              → step decision = pass
    """
    account_ctx = request.get("account_context", {})
    account_number = account_ctx.get("account_number", "unknown")
    settlement_flag = str(account_ctx.get("settlement_flag", "")).upper()

    evidence_bundle = request.get("evidence_bundle", {})
    step_id = request.get("step_id", "")
    rules = request.get("evaluation_rules", [])
    rule_ids = [r["rule_id"] for r in rules if "rule_id" in r]

    # Locate individual evidence checks from the bundle
    evidence_items: list[dict[str, Any]] = evidence_bundle.get("evidence", [])
    tag_check: dict[str, Any] = next(
        (e for e in evidence_items if e.get("check") == "account_tag_sif_presence"), {}
    )
    arlog_check: dict[str, Any] = next(
        (e for e in evidence_items if e.get("check") == "arlog_settlement_evidence"), {}
    )

    sif_present: bool = bool(tag_check.get("sif_present", False))
    settled_in_full_found: bool = bool(arlog_check.get("settled_in_full_found", False))
    latest_comment: str = arlog_check.get("latest_comment_message") or ""
    comment_implies_settlement: bool = any(
        phrase in latest_comment.lower()
        for phrase in ("settled", "settlement", "paid in full", "resolved")
    )

    used_checks: list[str] = []
    if tag_check:
        used_checks.append("account_tag_sif_presence")
    if arlog_check:
        used_checks.append("arlog_settlement_evidence")

    # Rule 1: SIF tag vs settlement_flag
    if settlement_flag == "Y":
        rule1_outcome = "pass" if sif_present else "manual_review"
    elif settlement_flag == "N":
        rule1_outcome = "fail" if sif_present else "pass"
    else:
        rule1_outcome = "manual_review"

    # Rule 2/3: AR log vs settlement_flag
    if settlement_flag == "Y":
        if settled_in_full_found or comment_implies_settlement:
            rule23_outcome = "pass"
        else:
            rule23_outcome = "insufficient_evidence"
    elif settlement_flag == "N":
        if settled_in_full_found or comment_implies_settlement:
            rule23_outcome = "fail"
        else:
            rule23_outcome = "pass"
    else:
        rule23_outcome = "manual_review"

    # Aggregate sub-outcomes (any_fail_fails policy)
    sub_outcomes = [rule1_outcome, rule23_outcome]
    if "fail" in sub_outcomes:
        step_decision = "fail"
    elif "insufficient_evidence" in sub_outcomes:
        step_decision = "insufficient_evidence"
    elif "manual_review" in sub_outcomes:
        step_decision = "manual_review"
    else:
        step_decision = "pass"

    reason = _build_step_reason(
        flag=settlement_flag,
        sif_present=sif_present,
        settled_found=settled_in_full_found,
        comment_implies=comment_implies_settlement,
        decision=step_decision,
    )

    logger.info(
        "step_decision_rules account_number=%s flag=%s rule1=%s rule23=%s decision=%s",
        account_number,
        settlement_flag,
        rule1_outcome,
        rule23_outcome,
        step_decision,
    )

    return {
        "decision_scope": "step_level",
        "step_id": step_id,
        "account_number": account_number,
        "decision": step_decision,
        "reason": reason,
        "used_rule_ids": rule_ids,
        "used_evidence_checks": used_checks,
    }


def _build_step_reason(
    flag: str,
    sif_present: bool,
    settled_found: bool,
    comment_implies: bool,
    decision: str,
) -> str:
    """Build a concise human-readable reason string for a step-level decision."""
    evidence_summary = (
        f"settlement_flag={flag}; "
        f"sif_present={sif_present}; "
        f"settled_in_full_found={settled_found}; "
        f"comment_implies_settlement={comment_implies}"
    )
    prefix_map = {
        "pass": "All applicable settlement rules passed.",
        "fail": "One or more settlement rules failed due to contradictory evidence.",
        "insufficient_evidence": "Insufficient evidence to confirm settlement status.",
        "manual_review": "Ambiguous evidence requires manual review.",
    }
    prefix = prefix_map.get(decision, "Decision outcome unclear.")
    return f"{prefix} {evidence_summary}."


def _apply_final_decision_rules(request: dict[str, Any]) -> dict[str, Any]:
    """Aggregate step-level decisions into a final account-level QC verdict.

    Aggregation policy (any_fail_fails_any_insufficient_manual_review):
        any fail                 → final = fail
        any insufficient/missing → final = manual_review
        any manual_review        → final = manual_review
        all pass                 → final = pass
    """
    account_ctx = request.get("account_context", {})
    account_number = account_ctx.get("account_number", "unknown")
    step_decisions: list[dict[str, Any]] = request.get("step_decisions", [])
    final_step_id = request.get("final_step_id", "")
    rules = request.get("evaluation_rules", [])
    rule_ids = [r["rule_id"] for r in rules if "rule_id" in r]

    used_step_ids = [sd.get("step_id") for sd in step_decisions if sd.get("step_id")]
    step_outcomes = [sd.get("decision") for sd in step_decisions]

    if "fail" in step_outcomes:
        final_decision = "fail"
        reason = "One or more required step-level decisions returned fail."
    elif any(o in ("insufficient_evidence", None) for o in step_outcomes):
        final_decision = "manual_review"
        reason = "One or more required step-level decisions returned insufficient evidence or are missing."
    elif "manual_review" in step_outcomes:
        final_decision = "manual_review"
        reason = "One or more required step-level decisions require manual review."
    else:
        final_decision = "pass"
        reason = "All required step-level decisions passed."

    logger.info(
        "final_decision_rules account_number=%s step_outcomes=%s decision=%s",
        account_number,
        step_outcomes,
        final_decision,
    )

    return {
        "decision_scope": "final_level",
        "final_step_id": final_step_id,
        "account_number": account_number,
        "decision": final_decision,
        "reason": reason,
        "used_rule_ids": rule_ids,
        "used_step_decisions": used_step_ids,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Output normalizers
# ──────────────────────────────────────────────────────────────────────────────

def summarize_step_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized, schema-conformant step-level decision dict."""
    decision = raw.get("decision", "manual_review")
    if decision not in VALID_STEP_DECISIONS:
        decision = "manual_review"
    return {
        "decision_scope": "step_level",
        "step_id": raw.get("step_id", ""),
        "account_number": raw.get("account_number", ""),
        "decision": decision,
        "reason": raw.get("reason", ""),
        "used_rule_ids": raw.get("used_rule_ids", []),
        "used_evidence_checks": raw.get("used_evidence_checks", []),
    }


def summarize_final_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized, schema-conformant final-level decision dict."""
    decision = raw.get("decision", "manual_review")
    if decision not in VALID_FINAL_DECISIONS:
        decision = "manual_review"
    return {
        "decision_scope": "final_level",
        "final_step_id": raw.get("final_step_id", ""),
        "account_number": raw.get("account_number", ""),
        "decision": decision,
        "reason": raw.get("reason", ""),
        "used_rule_ids": raw.get("used_rule_ids", []),
        "used_step_decisions": raw.get("used_step_decisions", []),
    }
