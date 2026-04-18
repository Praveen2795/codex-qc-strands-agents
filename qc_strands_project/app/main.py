"""Local entrypoint that proves the reusable QC workflow skeleton."""

from __future__ import annotations

import json
import logging
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
from app.agents.qc_decision_agent import build_qc_decision_agent
from app.agents.qc_validation_agent import build_qc_validation_agent
from app.logging_utils import setup_project_logging

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


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Run the reusable QC skeleton demonstration with live tracing."""
    result = demo_workflow()
    print_flow_trace(result)


if __name__ == "__main__":
    main()

