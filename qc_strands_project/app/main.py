"""Local entrypoint that proves the reusable QC workflow skeleton."""

from __future__ import annotations

import json
import logging
import sys
import textwrap
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
from app.logging_utils import setup_project_logging
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


# ── Live trace callback ───────────────────────────────────────────────────────

class ConsoleTraceCallbackHandler:
    """Prints each tool invocation and agent response to stdout as they happen."""

    TOOL_LABELS = {
        "fetch_structured_qc_data": ("pop-phase", _BLUE),
        "collect_qc_evidence":      ("evidence ", _MAGENTA),
        "make_qc_decision":         ("decision ", _YELLOW),
        "get_population_batch":     ("  tool   ", _DIM),
        "get_account_tag_sif_presence": ("  tool   ", _DIM),
        "get_arlog_settlement_evidence": ("  tool   ", _DIM),
    }

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._chunks: list[str] = []

    def __call__(self, **kwargs: Any) -> None:
        event = kwargs.get("event", {}) or {}
        data = kwargs.get("data", "")
        complete = kwargs.get("complete", False)

        tool_use = event.get("contentBlockStart", {}).get("start", {}).get("toolUse")
        if tool_use:
            name = tool_use.get("name", "?")
            label, colour = self.TOOL_LABELS.get(name, ("  call   ", _RESET))
            print(
                f"  {_c(f'[{label}]', colour, _BOLD)}"
                f"  {_c(self.agent_name, _DIM)} → {_c(name, _BOLD)}"
            )

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
                except (json.JSONDecodeError, TypeError):
                    pass
            self._chunks.clear()


# ── Main workflow ─────────────────────────────────────────────────────────────

def demo_workflow() -> dict:
    """Demonstrate the agent-driven orchestration flow with console tracing."""
    run_log_path = setup_project_logging()
    logger.info("demo_workflow_started")
    logger.info("demo_model_mode=local_deterministic")

    data_fetcher_agent = build_data_fetcher_agent()
    qc_validation_agent = build_qc_validation_agent()
    qc_decision_agent = build_qc_decision_agent()
    orchestrator_agent = build_orchestrator_agent(
        data_fetcher_agent,
        qc_validation_agent,
        qc_decision_agent,
    )

    # Swap in the console-trace callback handlers for this run
    data_fetcher_agent.callback_handler  = ConsoleTraceCallbackHandler("data_fetcher_agent")
    qc_validation_agent.callback_handler = ConsoleTraceCallbackHandler("qc_validation_agent")
    qc_decision_agent.callback_handler   = ConsoleTraceCallbackHandler("qc_decision_agent")
    orchestrator_agent.callback_handler  = ConsoleTraceCallbackHandler("orchestrator_agent")

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
    final_dec = checkpoint_result.get("final_decision") or {}
    logger.info(
        "demo_workflow_completed status=%s current_account=%s final_decision=%s",
        checkpoint_result.get("status"),
        checkpoint_result.get("current_account"),
        final_dec.get("decision") if isinstance(final_dec, dict) else final_dec,
    )

    return {
        "demo_request": demo_task["task_request"],
        "log_file": str(run_log_path),
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

def print_flow_trace(result: dict) -> None:
    """Print a human-readable step-by-step trace of the completed QC flow."""
    cr = result.get("checkpoint_result", {})
    outputs: list[dict] = cr.get("outputs", [])
    step_decision: dict = cr.get("step_decision", {})
    final_decision: dict = cr.get("final_decision", {})

    # ── Header ────────────────────────────────────────────────────────────────
    _section("QC FLOW — STEP-BY-STEP RESULTS")
    _field("procedure",      cr.get("procedure_name", "?"))
    _field("task_request",   result.get("demo_request", "?"))
    _field("batch_id",       cr.get("batch_id", "?"))
    _acct = cr.get("current_account", {})
    _acct_display = _acct.get("account_number", str(_acct)) if isinstance(_acct, dict) else str(_acct)
    _field("current_account", _c(_acct_display, _BOLD))
    _field("status",         _c(str(cr.get("status", "?")), _GREEN, _BOLD))

    # ── Population phase ──────────────────────────────────────────────────────
    pop_outputs = [o for o in outputs if o.get("phase") == "population_phase"]
    if pop_outputs:
        _section("PHASE 1 — POPULATION RETRIEVAL")
        for o in pop_outputs:
            _subsection(f"step {o['step_id']}  ·  agent: {o['agent_called']}")
            out = _parse_output(o.get("agent_output", {}))
            accounts = out.get("accounts", [])
            _field("accounts_returned", len(accounts))
            _field("next_cursor",       out.get("next_cursor"))
            _field("has_more",          out.get("has_more"))
            if accounts:
                print(f"    {_c('accounts:', _BOLD)}")
                for acc in accounts:
                    flag = acc.get("settlement_flag", "?")
                    flag_col = _GREEN if flag == "Y" else _RED
                    print(
                        f"      • {_c(acc.get('account_number', '?'), _BOLD)}"
                        f"  settlement_flag={_c(flag, flag_col, _BOLD)}"
                        f"  borrower={acc.get('borrower', '?')}"
                    )

    # ── Account phase ─────────────────────────────────────────────────────────
    acct_outputs = [o for o in outputs if o.get("phase") == "account_phase"]
    if acct_outputs:
        _section("PHASE 2 — PER-ACCOUNT PROCESSING")

        for o in acct_outputs:
            out = _parse_output(o.get("agent_output", {}))
            step_id = o["step_id"]
            agent = o["agent_called"]
            scope = out.get("decision_scope", "")

            # ── Evidence step ────────────────────────────────────────────────
            if agent == "collect_qc_evidence":
                _subsection(f"step {step_id}  ·  evidence collection  ·  account {out.get('account_number', '?')}")
                for ev in out.get("evidence", []):
                    check = ev.get("check", "?")
                    print(f"\n    {_c(f'  check: {check}', _BOLD)}")
                    print(_hr("·", 60))
                    if check == "account_tag_sif_presence":
                        present = ev.get("sif_present", False)
                        col = _GREEN if present else _RED
                        _field("sif_present",               _c(str(present), col, _BOLD))
                        _field("matching_sif_rows_count",   ev.get("matching_sif_rows_count", 0))
                        _field("matching_sif_tag_dates",    ev.get("matching_sif_tag_dates", []))
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

            # ── Step decision ────────────────────────────────────────────────
            elif agent == "make_qc_decision" and scope == "step_level":
                dec = out.get("decision", "?")
                col = _decision_colour(dec)
                _subsection(f"step {step_id}  ·  step-level decision  ·  account {out.get('account_number', '?')}")
                print()
                print(f"    {_c('DECISION:', _BOLD)}  {_c(dec.upper(), col, _BOLD)}")
                print()
                _field("reason",               out.get("reason", ""))
                _field("used_rule_ids",         out.get("used_rule_ids", []))
                _field("used_evidence_checks",  out.get("used_evidence_checks", []))

            # ── Final decision ───────────────────────────────────────────────
            elif agent == "make_qc_decision" and scope == "final_level":
                dec = out.get("decision", "?")
                col = _decision_colour(dec)
                _subsection(f"step {step_id}  ·  final decision  ·  account {out.get('account_number', '?')}")
                print()
                print(f"    {_c('FINAL VERDICT:', _BOLD)}  {_c(dec.upper(), col, _BOLD)}")
                print()
                _field("reason",               out.get("reason", ""))
                _field("used_rule_ids",         out.get("used_rule_ids", []))
                _field("used_step_decisions",   out.get("used_step_decisions", []))

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("SUMMARY")
    account = cr.get("current_account", "?")
    s_dec = step_decision.get("decision", "?")
    f_dec = final_decision.get("decision", "?")
    s_col = _decision_colour(s_dec)
    f_col = _decision_colour(f_dec)

    print(f"  account          : {_c(str(account), _BOLD)}")
    print(f"  step decision    : {_c(s_dec.upper(), s_col, _BOLD)}")
    print(f"  final verdict    : {_c(f_dec.upper(), f_col, _BOLD)}")
    print(f"  rules applied    : {step_decision.get('used_rule_ids', [])}")
    print(f"  evidence checks  : {step_decision.get('used_evidence_checks', [])}")
    print()
    _field("step reason",  step_decision.get("reason", ""))
    _field("final reason", final_decision.get("reason", ""))
    print()
    print(f"  log file  → {_c(result.get('log_file', '?'), _DIM)}")
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
    skipped_note = "rule_arlog_comment skipped — direct AR evidence present" if found else "(no rules skipped)"
    _field("fallback_status", _c(skipped_note, _DIM))

    _subsection("acct-3  ·  final verdict  (rule_final_aggregation)")
    print(f"    {_c('FINAL VERDICT:', _BOLD)}  {_c(dec_3.upper(), _decision_colour(dec_3), _BOLD)}")
    _field("reason",            final_decision.get("reason", ""))
    _field("step_decisions_in", [tag_decision.get("step_id"), arlog_decision.get("step_id")])

    print()
    print(_hr())
    print(f"  log file  → {_c(str(run_log_path), _DIM)}")
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
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run either the orchestrated QC demo or the local sequential demo.

    Usage:
      python -m app.main            # orchestrated (LLM orchestrator, default)
      python -m app.main local      # local sequential (direct tool chain, no orchestrator)
    """
    mode = sys.argv[1] if len(sys.argv) > 1 else "orchestrated"
    if mode == "local":
        run_local_sequential_demo()
    else:
        result = demo_workflow()
        print_flow_trace(result)


if __name__ == "__main__":
    main()

