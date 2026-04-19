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
import re
from typing import Any

from strands import Agent

from app.config import load_prompt
from app.logging_utils import ModelCallRetryHook, create_agent_callback_handler
from app.models.factory import build_default_agent_model

logger = logging.getLogger("qc_strands.agents.qc_decision")

# Regex-based settlement detection: match settlement keywords only when NOT negated.
# Looks at the 25-character window before each keyword for negation words (not, no, without, never).
_SETTLEMENT_KEYWORD_RE = re.compile(
    r"\b(settled|settlement|paid\s+in\s+full|resolved)\b", re.IGNORECASE
)
_NEGATION_PRECEDING_RE = re.compile(
    r"\b(not|no|without|never)\s+(\S+\s+)*$", re.IGNORECASE
)


def _comment_implies_settlement(text: str) -> bool:
    """Return True only when the comment positively asserts settlement.

    Prevents false positives from negated phrases such as
    'not final settlement' or 'no settlement identified'.
    """
    for m in _SETTLEMENT_KEYWORD_RE.finditer(text):
        preceding = text[max(0, m.start() - 25): m.start()]
        if _NEGATION_PRECEDING_RE.search(preceding):
            continue  # keyword is negated — skip
        return True
    return False


VALID_STEP_DECISIONS = frozenset({"pass", "fail", "manual_review", "error"})
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
        hooks=[ModelCallRetryHook(max_retries=3)],
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

    Evaluates ONLY the rules explicitly listed in `evaluation_rules` for the
    current step — no other rule logic is executed.

    Rule dispatch table:
        rule_sif_tag    — account tag SIF presence vs settlement_flag
        rule_arlog_direct   — AR log direct settled_in_full rows vs settlement_flag
        rule_arlog_comment  — AR log comment fallback (CONDITIONAL: skipped when
                              settled_in_full_found is true; skipping is not an error)

    Per-rule decision matrix:
        rule_sif_tag:
            flag=Y + sif_present=true  → pass
            flag=Y + sif_present=false → manual_review
            flag=N + sif_present=true  → fail
            flag=N + sif_present=false → pass

        rule_arlog_direct:
            flag=Y + settled_in_full_found=true  → pass
            flag=Y + settled_in_full_found=false → insufficient_evidence
            flag=N + settled_in_full_found=true  → fail
            flag=N + settled_in_full_found=false → pass

        rule_arlog_comment (only when settled_in_full_found=false):
            flag=Y + comment_implies_settlement=true  → pass
            flag=Y + comment_implies_settlement=false → insufficient_evidence
            flag=N + comment_implies_settlement=true  → fail
            flag=N + comment_implies_settlement=false → pass

    Step outcome aggregation (any_fail_fails policy):
        any fail                   → step decision = fail
        any insufficient_evidence  → step decision = insufficient_evidence
        any manual_review          → step decision = manual_review
        all pass                   → step decision = pass
    """
    account_ctx = request.get("account_context", {})
    account_number = account_ctx.get("account_number", "unknown")
    settlement_flag = str(account_ctx.get("settlement_flag", "")).upper()

    evidence_bundle = request.get("evidence_bundle", {})
    step_id = request.get("step_id", "")
    rules: list[dict[str, Any]] = request.get("evaluation_rules", [])
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
    comment_implies_settlement: bool = _comment_implies_settlement(latest_comment)

    # Build used_checks from evidence families referenced by the active rules only
    rule_id_set = set(rule_ids)
    used_checks: list[str] = []
    if ("rule_sif_tag" in rule_id_set) and tag_check:
        used_checks.append("account_tag_sif_presence")
    if ({"rule_arlog_direct", "rule_arlog_comment"} & rule_id_set) and arlog_check:
        used_checks.append("arlog_settlement_evidence")

    # Evaluate only the rules present in this step's evaluation_rules
    sub_outcomes: list[str] = []
    active_rule_ids: list[str] = []
    skipped_rule_ids: list[str] = []
    rule_outcomes: dict[str, str] = {}

    for rule in rules:
        rid = rule.get("rule_id")
        if not rid:
            continue

        if rid == "rule_sif_tag":
            if settlement_flag == "Y":
                outcome = "pass" if sif_present else "manual_review"
            elif settlement_flag == "N":
                outcome = "fail" if sif_present else "pass"
            else:
                outcome = "manual_review"
            sub_outcomes.append(outcome)
            active_rule_ids.append(rid)
            rule_outcomes[rid] = outcome

        elif rid == "rule_arlog_direct":
            if settlement_flag == "Y":
                outcome = "pass" if settled_in_full_found else "insufficient_evidence"
            elif settlement_flag == "N":
                outcome = "fail" if settled_in_full_found else "pass"
            else:
                outcome = "manual_review"
            sub_outcomes.append(outcome)
            active_rule_ids.append(rid)
            rule_outcomes[rid] = outcome

        elif rid == "rule_arlog_comment":
            # Conditional fallback: skip entirely when direct AR evidence already exists
            rule_type = rule.get("rule_type", "standard")
            if rule_type == "conditional_fallback" and settled_in_full_found:
                # Direct evidence satisfied the AR log check — fallback not needed
                logger.info(
                    "rule_skipped rule_id=%s reason=direct_ar_evidence_present account_number=%s",
                    rid,
                    account_number,
                )
                skipped_rule_ids.append(rid)
                rule_outcomes[rid] = "skipped"
                continue
            # Fallback applies — direct evidence is absent
            if settlement_flag == "Y":
                outcome = "pass" if comment_implies_settlement else "insufficient_evidence"
            elif settlement_flag == "N":
                outcome = "fail" if comment_implies_settlement else "pass"
            else:
                outcome = "manual_review"
            sub_outcomes.append(outcome)
            active_rule_ids.append(rid)
            rule_outcomes[rid] = outcome

        else:
            # Unknown rule — log and skip rather than fail silently
            logger.warning(
                "unknown_rule_id rule_id=%s step_id=%s account_number=%s — skipped",
                rid, step_id, account_number,
            )

    # Aggregate sub-outcomes (any_fail_fails policy)
    if not sub_outcomes:
        step_decision = "insufficient_evidence"
        reason = "No applicable rules were evaluated for this step."
    elif "fail" in sub_outcomes:
        step_decision = "fail"
    elif "insufficient_evidence" in sub_outcomes:
        step_decision = "insufficient_evidence"
    elif "manual_review" in sub_outcomes:
        step_decision = "manual_review"
    else:
        step_decision = "pass"

    if sub_outcomes:
        reason = _build_step_reason(
            flag=settlement_flag,
            sif_present=sif_present,
            settled_found=settled_in_full_found,
            comment_implies=comment_implies_settlement,
            decision=step_decision,
            active_rule_ids=active_rule_ids,
        )

    logger.info(
        "step_decision_rules account_number=%s flag=%s active_rules=%s sub_outcomes=%s decision=%s",
        account_number,
        settlement_flag,
        active_rule_ids,
        sub_outcomes,
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
        "skipped_rule_ids": skipped_rule_ids,
        "rule_outcomes": rule_outcomes,
    }


def _build_step_reason(
    flag: str,
    sif_present: bool,
    settled_found: bool,
    comment_implies: bool,
    decision: str,
    active_rule_ids: list[str] | None = None,
) -> str:
    """Build a concise human-readable reason string for a step-level decision."""
    rules_note = f" Rules evaluated: {active_rule_ids}." if active_rule_ids else ""
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
    return f"{prefix}{rules_note} {evidence_summary}."


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
        "skipped_rule_ids": raw.get("skipped_rule_ids", []),
        "rule_outcomes": raw.get("rule_outcomes", {}),
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
