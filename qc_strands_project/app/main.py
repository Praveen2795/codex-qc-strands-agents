"""Local entrypoint that proves the reusable QC workflow skeleton."""

from __future__ import annotations

import json
import logging
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import (
    compact_procedure_for_llm,
    load_schema_json,
    normalize_agent_tool_output,
    parse_json_response_text,
)
from app.agents.data_fetcher_agent import build_data_fetcher_agent
from app.agents.orchestrator_agent import (
    build_orchestrator_agent,
)
from app.agents.qc_decision_agent import build_qc_decision_agent, run_qc_decision_agent_wrapper
from app.agents.qc_validation_agent import build_qc_validation_agent
from app.logging_utils import setup_project_logging, CompositeCallbackHandler
from app.tools.arlog_tools import get_arlog_settlement_evidence
from app.tools.population_tools import get_population_batch
from app.tools.tag_tools import get_account_tag_sif_presence

logger = logging.getLogger("qc_strands.main")

# ── ANSI colour helpers ───────────────────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_BLUE   = "\033[34m"
_MAGENTA = "\033[35m"

def _c(text, *codes: str) -> str:
    return "".join(codes) + str(text) + _RESET

def _parse_output(agent_output: Any) -> Any:
    """Recursively unwrap {"output": [{"text": "..."}]} envelopes into parsed data."""
    if not isinstance(agent_output, dict):
        return agent_output
    output_list = agent_output.get("output")
    if isinstance(output_list, list) and output_list:
        first = output_list[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            try:
                parsed = parse_json_response_text(first["text"])
                return _parse_output(parsed)  # recurse for double-nested wrappers
            except (json.JSONDecodeError, ValueError):
                pass
    return agent_output

def _decision_colour(decision: str) -> str:
    mapping = {"pass": _GREEN, "fail": _RED, "manual_review": _YELLOW, "insufficient_evidence": _YELLOW}
    return mapping.get(decision, _RESET)

def _hr(char: str = "─", width: int = 72) -> str:
    return _c(char * width, _DIM)

def _section(title: str) -> None:
    print()
    print(_c(f"{'━' * 72}", _CYAN, _BOLD))
    print(_c(f"  {title}", _CYAN, _BOLD))
    print(_c(f"{'━' * 72}", _CYAN, _BOLD))

def _subsection(title: str) -> None:
    print()
    print(_c(f"  ┌─ {title}", _BLUE, _BOLD))

def _field(label: str, value: Any, indent: int = 4) -> None:
    pad = " " * indent
    if isinstance(value, (dict, list)):
        pretty = json.dumps(value, indent=2)
        lines = pretty.splitlines()
        print(f"{pad}{_c(label + ':', _BOLD)}  {lines[0]}")
        for line in lines[1:]:
            print(f"{pad}{'':>{len(label) + 2}}{line}")
    else:
        print(f"{pad}{_c(label + ':', _BOLD)}  {value}")

def _wrap(text: str, indent: int = 6, width: int = 68) -> None:
    pad = " " * indent
    for line in textwrap.wrap(str(text), width=width):
        print(f"{pad}{line}")


# ── JSONL persistence ─────────────────────────────────────────────────────────

def _persist_account_result(jsonl_path: Path, record: dict) -> None:
    """Append one account result as a JSON line to *jsonl_path* (create if needed).

    Each call is a single atomic write so partial-batch files remain valid even
    if the run is interrupted mid-batch.
    """
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


# ── Live trace callback ───────────────────────────────────────────────────────

class ConsoleTraceCallbackHandler:
    """Prints each tool invocation and agent response to stdout as they happen.

    Set verbose=True to also print the full JSON request/response payloads
    for sub-agent calls (collect_qc_evidence and make_qc_decision).
    """

    TOOL_LABELS = {
        "fetch_structured_qc_data": ("pop-phase", _BLUE),
        "collect_qc_evidence":      ("evidence ", _MAGENTA),
        "make_qc_decision":         ("decision ", _YELLOW),
        "get_population_batch":     ("  tool   ", _DIM),
        "get_account_tag_sif_presence": ("  tool   ", _DIM),
        "get_arlog_settlement_evidence": ("  tool   ", _DIM),
    }

    # Sub-agent tool names — show full payloads in verbose mode
    _AGENT_TOOLS = {"fetch_structured_qc_data", "collect_qc_evidence", "make_qc_decision"}

    def __init__(self, agent_name: str, *, verbose: bool = False) -> None:
        self.agent_name = agent_name
        self.verbose = verbose
        self._chunks: list[str] = []

    def __call__(self, **kwargs: Any) -> None:
        data = kwargs.get("data", "")
        complete = kwargs.get("complete", False)

        # current_tool_use is the official Strands kwarg (input is accumulated as streaming occurs)
        current_tool_use = kwargs.get("current_tool_use") or {}
        tool_name = current_tool_use.get("name")
        if tool_name:
            label, colour = self.TOOL_LABELS.get(tool_name, ("  call   ", _RESET))
            print(
                f"  {_c(f'[{label}]', colour, _BOLD)}"
                f"  {_c(self.agent_name, _DIM)} → {_c(tool_name, _BOLD)}"
            )
            if self.verbose and tool_name in self._AGENT_TOOLS:
                payload = current_tool_use.get("input") or {}
                if payload:
                    pretty = json.dumps(payload, indent=4, default=str)
                    print(_c("    ── REQUEST PAYLOAD ──────────────────────────────────────", _DIM))
                    for line in pretty.splitlines():
                        print(f"    {_c(line, _DIM)}")
                    print(_c("    ─────────────────────────────────────────────────────────", _DIM))

        if data:
            self._chunks.append(data)

        if complete:
            text = "".join(self._chunks).strip()
            if text:
                try:
                    parsed = json.loads(text)
                    # Show just the key decision fields live, not the full blob
                    if "decision" in parsed:
                        scope = parsed.get("decision_scope", "")
                        dec = parsed.get("decision", "")
                        colour = _decision_colour(dec)
                        print(
                            f"  {_c('[response ]', _GREEN, _BOLD)}"
                            f"  {_c(self.agent_name, _DIM)}"
                            f"  scope={_c(scope, _BOLD)}"
                            f"  decision={_c(dec.upper(), colour, _BOLD)}"
                        )
                    elif "accounts" in parsed:
                        count = len(parsed.get("accounts", []))
                        first = (parsed.get("accounts") or [{}])[0].get("account_number", "?")
                        print(
                            f"  {_c('[response ]', _GREEN, _BOLD)}"
                            f"  {_c(self.agent_name, _DIM)}"
                            f"  population_batch  accounts={count}  first={first}"
                        )
                    elif "evidence" in parsed:
                        checks = parsed.get("evidence_checks", [])
                        print(
                            f"  {_c('[response ]', _GREEN, _BOLD)}"
                            f"  {_c(self.agent_name, _DIM)}"
                            f"  evidence_bundle  checks={checks}"
                        )
                    if self.verbose:
                        print(_c("    ── RESPONSE PAYLOAD ─────────────────────────────────────", _DIM))
                        for line in json.dumps(parsed, indent=4, default=str).splitlines():
                            print(f"    {_c(line, _DIM)}")
                        print(_c("    ─────────────────────────────────────────────────────────", _DIM))
                except (json.JSONDecodeError, TypeError):
                    pass
            self._chunks.clear()


# ── Main workflow ─────────────────────────────────────────────────────────────

def demo_workflow(*, verbose: bool = False) -> dict:
    """Demonstrate the agent-driven orchestration flow with console tracing.

    Args:
        verbose: When True, print full JSON request/response payloads for every
                 sub-agent call during live execution and in the post-run trace.
    """
    run_log_path = setup_project_logging()
    logger.info("demo_workflow_started verbose=%s", verbose)
    logger.info("demo_model_mode=local_deterministic")

    jsonl_path = run_log_path.with_suffix(".jsonl")

    data_fetcher_agent = build_data_fetcher_agent()
    qc_validation_agent = build_qc_validation_agent()
    qc_decision_agent = build_qc_decision_agent()
    orchestrator_agent = build_orchestrator_agent(
        data_fetcher_agent,
        qc_validation_agent,
        qc_decision_agent,
    )

    # Use composite handlers so both the live console trace AND the file log
    # receive every callback event simultaneously.
    from app.logging_utils import create_agent_callback_handler
    def _cb(name: str) -> CompositeCallbackHandler:
        return CompositeCallbackHandler(
            ConsoleTraceCallbackHandler(name, verbose=verbose),
            create_agent_callback_handler(name),
        )

    data_fetcher_agent.callback_handler  = _cb("data_fetcher_agent")
    qc_validation_agent.callback_handler = _cb("qc_validation_agent")
    qc_decision_agent.callback_handler   = _cb("qc_decision_agent")
    orchestrator_agent.callback_handler  = _cb("orchestrator_agent")

    sample_procedure = load_schema_json("sample_procedure.json")
    # Pass a compact version to the LLM to stay within token budget.
    # The full procedure is kept in memory only for the post-run trace.
    procedure_for_llm = compact_procedure_for_llm(sample_procedure)
    demo_task: dict[str, object] = {
        "qc_name": sample_procedure["qc_name"],
        "procedure_name": sample_procedure["procedure_name"],
        "batch_id": "batch-001",
        "procedure_document": procedure_for_llm,
        "task_request": "Run settlement QC for February 2026",
        "start_date": "2026-02-01",
        "end_date": "2026-02-28",
    }

    _section("QC FLOW — LIVE TRACE")
    print(f"  procedure : {_c(sample_procedure['procedure_name'], _BOLD)}")
    print(f"  qc_name   : {_c(sample_procedure['qc_name'], _BOLD)}")
    print(f"  request   : {_c(str(demo_task['task_request']), _BOLD)}")
    print(f"  batch_id  : {_c(str(demo_task['batch_id']), _BOLD)}")
    print()
    print(_c("  Tool call trace:", _DIM))
    print(_hr())

    logger.info("demo_task=%s", json.dumps(demo_task, sort_keys=True))
    try:
        checkpoint_result = normalize_agent_tool_output(
            parse_json_response_text(str(orchestrator_agent(json.dumps(demo_task))))
        )
    except Exception:
        logger.exception("demo_workflow_failed")
        raise

    logger.info(
        "checkpoint_result=%s",
        json.dumps(checkpoint_result, sort_keys=True, default=str),
    )
    _ar = checkpoint_result.get("account_result") or {}
    logger.info(
        "demo_workflow_completed status=%s account=%s final_decision=%s",
        checkpoint_result.get("status"),
        _ar.get("account_number"),
        _ar.get("final_decision"),
    )

    # Persist per-account result immediately after the orchestrator completes.
    _acct_ctx = _ar.get("account_context") or {}
    _persist_account_result(jsonl_path, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_mode": "orchestrated",
        "procedure_name": checkpoint_result.get("procedure_name"),
        "batch_id": checkpoint_result.get("batch_id"),
        "account_number": _ar.get("account_number"),
        "settlement_flag": _acct_ctx.get("settlement_flag") if isinstance(_acct_ctx, dict) else None,
        "final_decision": _ar.get("final_decision"),
        "final_decision_reason": _ar.get("final_decision_reason"),
        "step_decisions": _ar.get("step_decisions", {}),
        "step_decision_reasons": _ar.get("step_decision_reasons", {}),
        "status": checkpoint_result.get("status"),
        "error": checkpoint_result.get("error"),
    })

    return {
        "demo_request": demo_task["task_request"],
        "log_file": str(run_log_path),
        "jsonl_file": str(jsonl_path),
        "agents": {
            "orchestrator": orchestrator_agent.name,
            "data_fetcher": data_fetcher_agent.name,
            "qc_validation": qc_validation_agent.name,
            "qc_decision": qc_decision_agent.name,
        },
        "registered_tools": {
            "data_fetcher": ["get_population_batch"],
            "qc_validation": ["get_account_tag_sif_presence", "get_arlog_settlement_evidence"],
            "qc_decision": [],
            "orchestrator": ["fetch_structured_qc_data", "collect_qc_evidence", "make_qc_decision"],
        },
        "checkpoint_result": checkpoint_result,
    }


# ── Post-run pretty trace ─────────────────────────────────────────────────────

def print_flow_trace(result: dict, *, verbose: bool = False) -> None:
    """Print a human-readable step-by-step trace of the completed QC flow."""
    cr = result.get("checkpoint_result", {})
    status = cr.get("status", "?")
    ar = cr.get("account_result") or {}

    # ── Header ────────────────────────────────────────────────────────────────
    _section("QC FLOW — STEP-BY-STEP RESULTS")
    _field("procedure",    cr.get("procedure_name", "?"))
    _field("task_request", result.get("demo_request", "?"))
    _field("batch_id",     cr.get("batch_id", "?"))
    status_col = _GREEN if status == "completed" else _RED
    _field("status", _c(status.upper(), status_col, _BOLD))

    # ── Execution error ───────────────────────────────────────────────────────
    if status == "error":
        err = cr.get("error") or {}
        _section("EXECUTION ERROR")
        _field("type",    err.get("type", "unknown"))
        _field("message", err.get("message", ""))
        if err.get("step_id"):
            _field("failed_at_step", err["step_id"])
        print()
        print(_hr())
        return

    # ── Account context ───────────────────────────────────────────────────────
    _section("ACCOUNT")
    _field("account_number", _c(str(ar.get("account_number", "?")), _BOLD))
    acct_ctx = ar.get("account_context") or {}
    if isinstance(acct_ctx, dict):
        for k, v in acct_ctx.items():
            if k != "account_number":
                _field(k, v)

    # ── Evidence collected ────────────────────────────────────────────────────
    step_outputs: dict = ar.get("step_outputs") or {}
    if step_outputs:
        _section("PHASE 1 — EVIDENCE COLLECTED")
        for step_id, raw_bundle in sorted(step_outputs.items()):
            out = _parse_output(raw_bundle)
            _subsection(f"step {step_id}  ·  evidence collection  ·  account {out.get('account_number', '?')}")
            if verbose:
                print(_c("    ── FULL EVIDENCE BUNDLE ─────────────────────────────────", _DIM))
                for line in json.dumps(out, indent=4, default=str).splitlines():
                    print(f"    {_c(line, _DIM)}")
                print(_c("    ─────────────────────────────────────────────────────────", _DIM))
            else:
                for ev in out.get("evidence", []):
                    check = ev.get("check", "?")
                    print(f"\n    {_c(f'  check: {check}', _BOLD)}")
                    print(_hr("·", 60))
                    if check == "account_tag_sif_presence":
                        present = ev.get("sif_present", False)
                        col = _GREEN if present else _RED
                        _field("sif_present",             _c(str(present), col, _BOLD))
                        _field("matching_sif_rows_count", ev.get("matching_sif_rows_count", 0))
                        _field("matching_sif_tag_dates",  ev.get("matching_sif_tag_dates", []))
                    elif check == "arlog_settlement_evidence":
                        found = ev.get("settled_in_full_found", False)
                        col = _GREEN if found else _DIM
                        _field("settled_in_full_found",         _c(str(found), col, _BOLD))
                        _field("matching_rows_count",           ev.get("matching_settled_in_full_rows_count", 0))
                        _field("comment_check_performed",       ev.get("comment_check_performed", False))
                        _field("latest_comment_message",        ev.get("latest_comment_message") or "(none)")
                        rows = ev.get("matching_settled_in_full_rows", [])
                        if rows:
                            print(f"    {_c('matching_settled_in_full_rows:', _BOLD)}")
                            for row in rows:
                                print(f"      • {row.get('timestamp', '?')}  {row.get('message', '')!r}")

    # ── Step decisions ────────────────────────────────────────────────────────
    step_decisions: dict = ar.get("step_decisions") or {}
    step_decision_reasons: dict = ar.get("step_decision_reasons") or {}
    if step_decisions:
        _section("PHASE 2 — STEP DECISIONS")
        for step_id, decision in sorted(step_decisions.items()):
            dec = str(decision)
            col = _decision_colour(dec)
            reason = step_decision_reasons.get(step_id, "")
            _subsection(f"step {step_id}  ·  step decision  ·  account {ar.get('account_number', '?')}")
            print()
            print(f"    {_c('DECISION:', _BOLD)}  {_c(dec.upper(), col, _BOLD)}")
            print()
            _field("reason", reason)

    # ── Final verdict ─────────────────────────────────────────────────────────
    final_decision = str(ar.get("final_decision", "?"))
    final_reason = str(ar.get("final_decision_reason", ""))
    final_col = _decision_colour(final_decision)
    _section("FINAL VERDICT")
    print()
    print(f"    {_c('FINAL VERDICT:', _BOLD)}  {_c(final_decision.upper(), final_col, _BOLD)}")
    print()
    _field("reason", final_reason)

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("SUMMARY")
    print(f"  account          : {_c(str(ar.get('account_number', '?')), _BOLD)}")
    print(f"  final verdict    : {_c(final_decision.upper(), final_col, _BOLD)}")
    if step_decisions:
        for step_id, dec in sorted(step_decisions.items()):
            dec_str = str(dec)
            col = _decision_colour(dec_str)
            print(f"  {step_id:<20}: {_c(dec_str.upper(), col, _BOLD)}")
    print()
    print(f"  log file  → {_c(result.get('log_file', '?'), _DIM)}")
    if result.get("jsonl_file"):
        print(f"  results   → {_c(result['jsonl_file'], _DIM)}")
    print()
    print(_hr())


# ── Local sequential demo (no orchestrator) ──────────────────────────────────

def run_local_sequential_demo() -> dict:
    """Verify the 6-step QC flow by directly chaining tools and decision wrapper.

    Bypasses the LLM orchestrator entirely. Each step is called explicitly:
      pop-1   → get_population_batch                (data tool, direct)
      acct-1a → get_account_tag_sif_presence        (evidence tool, direct)
      acct-1b → run_qc_decision_agent_wrapper       (rule_sif_tag only)
      acct-2a → get_arlog_settlement_evidence       (evidence tool, direct)
      acct-2b → run_qc_decision_agent_wrapper       (rule_arlog_direct + conditional rule_arlog_comment)
      acct-3  → run_qc_decision_agent_wrapper       (final aggregation)
    """
    run_log_path = setup_project_logging("local_sequential_demo")
    logger.info("local_sequential_demo_started")

    jsonl_path = run_log_path.with_suffix(".jsonl")

    sample_procedure = load_schema_json("sample_procedure.json")
    evaluation_rules = sample_procedure.get("evaluation_rules", [])
    rules_by_id = {r["rule_id"]: r for r in evaluation_rules}

    def _rules(ids: list[str]) -> list[dict]:
        return [rules_by_id[i] for i in ids if i in rules_by_id]

    _section("LOCAL SEQUENTIAL DEMO — DIRECT TOOL CHAIN (NO ORCHESTRATOR)")
    print(f"  procedure : {_c(sample_procedure['procedure_name'], _BOLD)}")
    print(f"  mode      : {_c('deterministic tools · rule-subset-aware decisions', _DIM)}")
    print()
    print(_c("  Step trace:", _DIM))
    print(_hr())

    # ── pop-1: fetch population batch ─────────────────────────────────────────
    print(f"  {_c('[pop-1   ]', _BLUE, _BOLD)}  get_population_batch")
    pop_result = get_population_batch(
        start_date="2026-02-01",
        end_date="2026-02-28",
        cursor=0,
        batch_size=2,
    )
    accounts = pop_result.get("accounts", [])
    account_data = accounts[0] if accounts else {}
    account_number = str(account_data.get("account_number", "?"))
    settlement_flag = str(account_data.get("settlement_flag", "?"))
    account_context = {"account_number": account_number, "settlement_flag": settlement_flag}
    logger.info("pop1_complete accounts=%d first=%s flag=%s", len(accounts), account_number, settlement_flag)

    # ── acct-1a: SIF tag evidence ─────────────────────────────────────────────
    print(f"  {_c('[acct-1a ]', _MAGENTA, _BOLD)}  get_account_tag_sif_presence  acct={account_number}")
    tag_evidence = get_account_tag_sif_presence(account_number=account_number)
    tag_bundle = {
        "account_number": account_number,
        "evidence_count": 1,
        "evidence_checks": [tag_evidence.get("check")],
        "evidence": [tag_evidence],
    }
    logger.info("acct_1a_complete sif_present=%s", tag_evidence.get("sif_present"))

    # ── acct-1b: tag step decision — rule_sif_tag only ────────────────────────
    print(f"  {_c('[acct-1b ]', _YELLOW, _BOLD)}  step_decision  rule_sif_tag only")
    tag_decision = run_qc_decision_agent_wrapper({
        "decision_mode": "step_decision",
        "step_id": "acct-1b",
        "step_title": "Evaluate SIF tag evidence",
        "account_context": account_context,
        "evidence_bundle": tag_bundle,
        "evaluation_rules": _rules(["rule_sif_tag"]),
    })
    dec_1b = tag_decision.get("decision", "?")
    print(f"  {_c('[acct-1b ]', _YELLOW, _BOLD)}  → {_c(dec_1b.upper(), _decision_colour(dec_1b), _BOLD)}")
    logger.info("acct_1b_complete decision=%s rules=%s", dec_1b, tag_decision.get("used_rule_ids"))

    # ── acct-2a: AR log evidence ──────────────────────────────────────────────
    print(f"  {_c('[acct-2a ]', _MAGENTA, _BOLD)}  get_arlog_settlement_evidence  acct={account_number}")
    arlog_evidence = get_arlog_settlement_evidence(account_number=account_number)
    arlog_bundle = {
        "account_number": account_number,
        "evidence_count": 1,
        "evidence_checks": [arlog_evidence.get("check")],
        "evidence": [arlog_evidence],
    }
    logger.info(
        "acct_2a_complete settled_in_full_found=%s comment_check_performed=%s",
        arlog_evidence.get("settled_in_full_found"),
        arlog_evidence.get("comment_check_performed"),
    )

    # ── acct-2b: AR log step decision — rule_arlog_direct + conditional rule_arlog_comment
    print(f"  {_c('[acct-2b ]', _YELLOW, _BOLD)}  step_decision  rule_arlog_direct + conditional rule_arlog_comment")
    arlog_decision = run_qc_decision_agent_wrapper({
        "decision_mode": "step_decision",
        "step_id": "acct-2b",
        "step_title": "Evaluate AR log settlement evidence",
        "account_context": account_context,
        "evidence_bundle": arlog_bundle,
        "evaluation_rules": _rules(["rule_arlog_direct", "rule_arlog_comment"]),
    })
    dec_2b = arlog_decision.get("decision", "?")
    print(f"  {_c('[acct-2b ]', _YELLOW, _BOLD)}  → {_c(dec_2b.upper(), _decision_colour(dec_2b), _BOLD)}")
    logger.info("acct_2b_complete decision=%s rules=%s", dec_2b, arlog_decision.get("used_rule_ids"))

    # ── acct-3: final decision ────────────────────────────────────────────────
    print(f"  {_c('[acct-3  ]', _YELLOW, _BOLD)}  final_decision  rule_final_aggregation")
    final_decision = run_qc_decision_agent_wrapper({
        "decision_mode": "final_decision",
        "final_step_id": "acct-3",
        "account_context": account_context,
        "step_decisions": [tag_decision, arlog_decision],
        "evaluation_rules": _rules(["rule_final_aggregation"]),
    })
    dec_3 = final_decision.get("decision", "?")
    print(f"  {_c('[acct-3  ]', _YELLOW, _BOLD)}  → {_c(dec_3.upper(), _decision_colour(dec_3), _BOLD)}")
    logger.info("acct_3_complete final_decision=%s", dec_3)

    # ── Print results ─────────────────────────────────────────────────────────
    _section("LOCAL SEQUENTIAL DEMO — RESULTS")
    _field("account", f"{account_number}  flag={settlement_flag}  borrower={account_data.get('borrower', '?')}")

    _subsection("acct-1a  ·  SIF tag evidence")
    sif = tag_evidence.get("sif_present", False)
    _field("sif_present",            _c(str(sif), _GREEN if sif else _RED, _BOLD))
    _field("matching_sif_rows_count", tag_evidence.get("matching_sif_rows_count", 0))

    _subsection("acct-1b  ·  tag step decision  (rule_sif_tag only)")
    print(f"    {_c('DECISION:', _BOLD)}  {_c(dec_1b.upper(), _decision_colour(dec_1b), _BOLD)}")
    _field("reason",        tag_decision.get("reason", ""))
    _field("used_rule_ids", tag_decision.get("used_rule_ids", []))

    _subsection("acct-2a  ·  AR log evidence")
    found = arlog_evidence.get("settled_in_full_found", False)
    _field("settled_in_full_found",   _c(str(found), _GREEN if found else _DIM, _BOLD))
    _field("matching_rows_count",     arlog_evidence.get("matching_settled_in_full_rows_count", 0))
    _field("comment_check_performed", arlog_evidence.get("comment_check_performed", False))

    _subsection("acct-2b  ·  AR log step decision  (rule_arlog_direct + conditional rule_arlog_comment)")
    print(f"    {_c('DECISION:', _BOLD)}  {_c(dec_2b.upper(), _decision_colour(dec_2b), _BOLD)}")
    _field("reason",        arlog_decision.get("reason", ""))
    _field("used_rule_ids", arlog_decision.get("used_rule_ids", []))
    skipped = arlog_decision.get("skipped_rule_ids", [])
    skipped_note = f"skipped: {skipped}" if skipped else "(no rules skipped)"
    _field("fallback_status",   _c(skipped_note, _DIM))
    _field("tag rule_outcomes", tag_decision.get("rule_outcomes", {}))
    _field("ar  rule_outcomes", arlog_decision.get("rule_outcomes", {}))

    _subsection("acct-3  ·  final verdict  (rule_final_aggregation)")
    print(f"    {_c('FINAL VERDICT:', _BOLD)}  {_c(dec_3.upper(), _decision_colour(dec_3), _BOLD)}")
    _field("reason",            final_decision.get("reason", ""))
    _field("step_decisions_in", [tag_decision.get("step_id"), arlog_decision.get("step_id")])

    print()
    print(_hr())
    print(f"  log file  → {_c(str(run_log_path), _DIM)}")
    print()

    _persist_account_result(jsonl_path, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_mode": "local",
        "account_number": account_number,
        "settlement_flag": settlement_flag,
        "borrower": account_data.get("borrower", ""),
        "final_decision": dec_3,
        "step_decisions": {
            "tag": dec_1b,
            "arlog": dec_2b,
        },
        "rule_outcomes": {
            "tag": tag_decision.get("rule_outcomes", {}),
            "arlog": arlog_decision.get("rule_outcomes", {}),
        },
        "skipped_rule_ids": arlog_decision.get("skipped_rule_ids", []),
    })
    print(f"  results   → {_c(str(jsonl_path), _DIM)}")
    print()

    logger.info(
        "local_sequential_demo_completed account=%s tag_dec=%s arlog_dec=%s final_dec=%s",
        account_number, dec_1b, dec_2b, dec_3,
    )
    return {
        "demo_type": "local_sequential",
        "account": account_data,
        "tag_evidence": tag_evidence,
        "tag_decision": tag_decision,
        "arlog_evidence": arlog_evidence,
        "arlog_decision": arlog_decision,
        "final_decision": final_decision,
        "log_file": str(run_log_path),
        "jsonl_file": str(jsonl_path),
    }


# ── Multi-account validation test ─────────────────────────────────────────────

_TEST_ACCOUNTS = [
    {"account_number": "100001", "settlement_flag": "Y", "borrower": "Alex Johnson",  "expected_final": "pass"},
    {"account_number": "100010", "settlement_flag": "N", "borrower": "Hayden Flores", "expected_final": "fail"},
    {"account_number": "100004", "settlement_flag": "Y", "borrower": "Riley Cooper",  "expected_final": "manual_review"},
    {"account_number": "100005", "settlement_flag": "Y", "borrower": "Avery Patel",   "expected_final": "manual_review"},
]


def run_multi_account_test() -> list[dict]:
    """Run the 6-step deterministic QC flow over 4 targeted test accounts.

    Validates all meaningful outcome paths without the LLM orchestrator:
        100001  flag=Y  SIF tag + direct AR evidence          → PASS
        100010  flag=N  SIF tag + ambiguous AR comment        → FAIL
        100004  flag=Y  no SIF tag + comment-only AR evidence → MANUAL_REVIEW
        100005  flag=Y  SIF tag + no direct AR rows           → MANUAL_REVIEW (via step insufficient_evidence)
    """
    run_log_path = setup_project_logging("multi_account_test")
    logger.info("multi_account_test_started accounts=%d", len(_TEST_ACCOUNTS))

    jsonl_path = run_log_path.with_suffix(".jsonl")

    sample_procedure = load_schema_json("sample_procedure.json")
    evaluation_rules = sample_procedure.get("evaluation_rules", [])
    rules_by_id = {r["rule_id"]: r for r in evaluation_rules}

    def _rules(ids: list[str]) -> list[dict]:
        return [rules_by_id[i] for i in ids if i in rules_by_id]

    _section("MULTI-ACCOUNT VALIDATION TEST")
    print(f"  Testing {len(_TEST_ACCOUNTS)} accounts  ·  deterministic rule chain  ·  no LLM orchestrator")
    print()
    print(_c("  Step trace:", _DIM))
    print(_hr())

    results = []
    for acct in _TEST_ACCOUNTS:
        account_number = acct["account_number"]
        settlement_flag = acct["settlement_flag"]
        expected_final = acct["expected_final"]
        account_context = {"account_number": account_number, "settlement_flag": settlement_flag}

        tag_evidence = get_account_tag_sif_presence(account_number=account_number)
        tag_bundle = {
            "account_number": account_number,
            "evidence_count": 1,
            "evidence_checks": [tag_evidence.get("check")],
            "evidence": [tag_evidence],
        }
        tag_decision = run_qc_decision_agent_wrapper({
            "decision_mode": "step_decision",
            "step_id": "acct-1b",
            "step_title": "Evaluate SIF tag evidence",
            "account_context": account_context,
            "evidence_bundle": tag_bundle,
            "evaluation_rules": _rules(["rule_sif_tag"]),
        })

        arlog_evidence = get_arlog_settlement_evidence(account_number=account_number)
        arlog_bundle = {
            "account_number": account_number,
            "evidence_count": 1,
            "evidence_checks": [arlog_evidence.get("check")],
            "evidence": [arlog_evidence],
        }
        arlog_decision = run_qc_decision_agent_wrapper({
            "decision_mode": "step_decision",
            "step_id": "acct-2b",
            "step_title": "Evaluate AR log settlement evidence",
            "account_context": account_context,
            "evidence_bundle": arlog_bundle,
            "evaluation_rules": _rules(["rule_arlog_direct", "rule_arlog_comment"]),
        })

        final = run_qc_decision_agent_wrapper({
            "decision_mode": "final_decision",
            "final_step_id": "acct-3",
            "account_context": account_context,
            "step_decisions": [tag_decision, arlog_decision],
            "evaluation_rules": _rules(["rule_final_aggregation"]),
        })

        final_dec = final.get("decision", "?")
        matched = final_dec == expected_final
        results.append({
            "account_number": account_number,
            "settlement_flag": settlement_flag,
            "borrower": acct["borrower"],
            "expected_final": expected_final,
            "tag_decision": tag_decision,
            "arlog_decision": arlog_decision,
            "final_decision": final,
            "matched": matched,
        })

        _persist_account_result(jsonl_path, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_mode": "test",
            "account_number": account_number,
            "settlement_flag": settlement_flag,
            "borrower": acct["borrower"],
            "expected_final": expected_final,
            "final_decision": final.get("decision"),
            "step_decisions": {
                "tag": tag_decision.get("decision"),
                "arlog": arlog_decision.get("decision"),
            },
            "rule_outcomes": {
                "tag": tag_decision.get("rule_outcomes", {}),
                "arlog": arlog_decision.get("rule_outcomes", {}),
            },
            "skipped_rule_ids": arlog_decision.get("skipped_rule_ids", []),
            "matched": matched,
        })

        tag_dec = tag_decision.get("decision", "?")
        ar_dec = arlog_decision.get("decision", "?")
        ar_skipped = arlog_decision.get("skipped_rule_ids", [])
        flag_col = _GREEN if settlement_flag == "Y" else _RED
        skipped_note = f"  {_c(f'[skipped: {ar_skipped}]', _DIM)}" if ar_skipped else ""
        print(
            f"  {_c(account_number, _BOLD)}"
            f"  flag={_c(settlement_flag, flag_col, _BOLD)}"
            f"  {acct['borrower']:<20}"
            f"  1b={_c(tag_dec.upper(), _decision_colour(tag_dec), _BOLD)}"
            f"  2b={_c(ar_dec.upper(), _decision_colour(ar_dec), _BOLD)}"
            f"{skipped_note}"
            f"  final={_c(final_dec.upper(), _decision_colour(final_dec), _BOLD)}"
            f"  expect={_c(expected_final.upper(), _decision_colour(expected_final))}"
            f"  {_c('OK', _GREEN, _BOLD) if matched else _c('MISMATCH', _RED, _BOLD)}"
        )

    print()
    all_pass = all(r["matched"] for r in results)
    n_ok = sum(1 for r in results if r["matched"])
    overall = (
        _c(f"ALL {n_ok}/{len(results)} TESTS PASSED", _GREEN, _BOLD)
        if all_pass
        else _c(f"{n_ok}/{len(results)} PASSED — {len(results)-n_ok} MISMATCH(ES)", _RED, _BOLD)
    )
    print(f"  Result: {overall}")

    # ── Per-account rule outcome detail ───────────────────────────────────────
    _section("TEST RESULTS — RULE OUTCOMES PER ACCOUNT")
    for r in results:
        acct_num = r["account_number"]
        s_flag = r["settlement_flag"]
        f_dec = r["final_decision"].get("decision", "?")
        flag_col = _GREEN if s_flag == "Y" else _RED
        matched = r["matched"]
        _subsection(
            f"{acct_num}  flag={_c(s_flag, flag_col, _BOLD)}"
            f"  borrower={r['borrower']}"
            f"  final={_c(f_dec.upper(), _decision_colour(f_dec), _BOLD)}"
            f"  expected={_c(r['expected_final'].upper(), _decision_colour(r['expected_final']))}"
            f"  {_c('OK', _GREEN, _BOLD) if matched else _c('MISMATCH', _RED, _BOLD)}"
        )
        _field("tag rule_outcomes",   r["tag_decision"].get("rule_outcomes", {}))
        _field("arlog rule_outcomes", r["arlog_decision"].get("rule_outcomes", {}))
        skipped = r["arlog_decision"].get("skipped_rule_ids", [])
        if skipped:
            _field("arlog skipped_rules", skipped)

    print()
    print(f"  log file  → {_c(str(run_log_path), _DIM)}")
    print(f"  results   → {_c(str(jsonl_path), _DIM)}")
    print()
    print(_hr())
    logger.info(
        "multi_account_test_completed all_pass=%s results=%s",
        all_pass,
        [{r["account_number"]: r["final_decision"].get("decision")} for r in results],
    )
    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run either the orchestrated QC demo or the local sequential demo.

    Usage:
      python -m app.main            # orchestrated (LLM orchestrator, default)
      python -m app.main local      # local sequential (direct tool chain, no orchestrator)
      python -m app.main test       # multi-account test (4 accounts, all outcome paths)
      python -m app.main -v         # orchestrated + verbose I/O (full agent payloads)
      python -m app.main local -v   # local sequential (verbose flag is ignored for local mode)
    """
    args = sys.argv[1:]
    verbose = "-v" in args or "--verbose" in args
    mode_args = [a for a in args if a not in ("-v", "--verbose")]
    mode = mode_args[0] if mode_args else "orchestrated"

    if mode == "local":
        run_local_sequential_demo()
    elif mode == "test":
        run_multi_account_test()
    else:
        result = demo_workflow(verbose=verbose)
        print_flow_trace(result, verbose=verbose)


if __name__ == "__main__":
    main()

