# QC Strands Project

Checkpoint-1 prototype for a reusable QC workflow built with Strands agents.

## Current State

This repository is a working local prototype for the first milestone. It includes:

- a procedure-driven orchestrator
- a data fetcher agent implemented as a phase-1 thin wrapper
- a QC validation agent implemented as a phase-1 thin wrapper
- placeholder retrieval and evidence tools
- a local demo flow that runs end to end with placeholder data

The current implementation is intentionally limited to local execution and placeholder logic. It does not include production integrations, persistence, retries, scaling, or deployment concerns.

## What Is Included

- `app/main.py` runs the checkpoint-1 local demo
- `app/agents/` contains the orchestrator, data fetcher, and QC validation agents
- `app/tools/` contains placeholder Strands tools for population, tag, and AR log evidence
- `app/prompts/` contains narrow role-based prompts
- `app/schemas/internal_recovery_potential_settlements_procedure.json` contains the procedure used by the local demo
- `app/schemas/response_examples.py` contains shared response models and placeholder schema notes

## Phase-1 Thin Wrappers

For checkpoint 1, the specialized agents are still used through thin wrappers around their registered tool surfaces.

- The orchestrator decides which agent to call based on the procedure.
- The data fetcher wrapper uses retrieval tools only.
- The QC validation wrapper uses evidence tools only.

This means the repo already demonstrates the intended workflow boundaries, while keeping the runtime simple and local. A future phase can replace these wrappers with fuller model-driven agent execution without changing the high-level role separation.

## Demo Flow

The local demo currently validates this path:

1. load a sample settlement QC procedure from JSON
2. let the orchestrator interpret the first steps
3. call the data fetcher agent for one population batch
4. select the first account only
5. call the QC validation agent for evidence collection
6. print structured outputs

## Local Edge Cases

The local data layer is intentionally mixed so the agent flow can be stress-tested before real integrations.

- `100001`: clean settled case
- `100002`: clean not-settled case
- `100003`: contradiction where population says `N` but SIF tag exists
- `100004`: no direct AR evidence, latest comment strongly implies settlement
- `100005`: ambiguous latest comment with otherwise positive-looking data
- `100006`: multiple comments where the latest weaker comment should drive fallback review
- `100007`: population says `Y` but downstream evidence is sparse
- `100008`: population says `N` but latest comment strongly implies settlement
- `100009`: multiple historical positive records across tags and AR logs
- `100010`: incomplete downstream rows with missing tag date and null AR message
- `100011`: partial SIF evidence with missing tag date
- `100012`: population-only account with no downstream records
- `100013`: downstream noise only, no QC-relevant evidence
- `100014`: ambiguous historical comments with a weak latest comment
- `100015`: customer-reported future zero balance language
- `100016`: direct AR log contradiction against population `N`
- `199001`: noise account that exists downstream but not in population

See `app/data/test_case_catalog.json` for the full scenario mapping and testing purpose of each sample account.

---

## Onboarding a New QC

The framework is procedure-driven. The agents are fully generic and reusable as-is. To onboard a new QC you only need to:

1. Write a procedure JSON
2. Implement the evidence tools for that QC
3. Wire up the tools in `main.py`

Everything else — orchestration, decision logic, aggregation, output schema — is handled automatically.

---

### Step 1 — Write the Procedure JSON

Create a new file in `app/schemas/`, e.g. `app/schemas/charge_off_review_procedure.json`.

The procedure must follow the same structure as `app/schemas/internal_recovery_potential_settlements_procedure.json`. The required top-level sections are:

| Section | What to fill in |
|---|---|
| `qc_name` | Short unique identifier for this QC, e.g. `"charge_off_review_qc"` |
| `procedure_name` | Human-readable name shown in run output |
| `description` | What this QC validates |
| `unit_of_work` | Always `"account"` for account-level QCs |
| `orchestration_mode` | Always `"dynamic"` |
| `agents` | Keep the three agent entries unchanged — only the `tool_name` values can be customized if needed |
| `population_phase` | Define one `data_retrieval` step: the tool to call, the output fields to extract |
| `account_phase` | Define the evidence collection steps, step decision steps, and one final decision step |
| `evaluation_rules` | One rule per check: define the `decision_matrix` rows that map evidence values to `pass`/`fail`/`manual_review` |
| `decision_policy` | Set `step_aggregation_policy` and `final_aggregation_policy`. Use `"any_fail_fails"` and `"any_fail_fails_any_manual_review_manual_review"` unless you have a specific reason to deviate |
| `result_schema` | Update `account_level_fields` to reflect the output fields specific to this QC |
| `checkpoint_scope` | Set `batch_size` and `max_accounts_to_process` for local testing |

**Procedure step pattern — each account check is always a pair of steps:**

```
acct-Na  (step_type: evidence_collection)  →  collected by qc_validation_agent
acct-Nb  (step_type: step_decision)        →  evaluated by qc_decision_agent
```

Finish every account with a single `final_decision` step that aggregates all the `step_decision` outputs.

**`depends_on` rules:**
- Every `evidence_collection` step must depend on `pop-1`
- Every `step_decision` step must depend on `pop-1` and its paired `evidence_collection` step
- The `final_decision` step must depend on all `step_decision` steps

---

### Step 2 — Implement the Evidence Tools

For each `evidence_collection` step in your procedure, you need one corresponding Strands tool.

Create a new file in `app/tools/`, e.g. `app/tools/charge_off_tools.py`:

```python
from strands import tool
from app.utils.data_loader import load_my_data   # add a loader for your data source

@tool
def get_charge_off_status(account_number: str) -> dict:
    """Return charge-off status evidence for one account.

    Args:
        account_number: Account being checked.
    """
    # Query your data source and return a flat dict.
    # The keys in this dict are what you reference in evaluation_rules[].evidence_field.
    return {
        "account_number": account_number,
        "check": "charge_off_status",
        "charge_off_found": ...,
        # add any other fields your rules need
    }
```

**Rules for tools:**
- Decorate with `@tool` from `strands`
- Accept `account_number: str` as a parameter (the orchestrator always passes it)
- Return a plain `dict` — no nested agents, no decisions
- The tool name in the function definition must exactly match the name used in `evidence_tools` in the procedure step

---

### Step 3 — Add a Data Loader (if needed)

If your tools need to read from a local data file during development/testing, add a loader function to `app/utils/data_loader.py`:

```python
def load_charge_off_data() -> list[dict]:
    return _load_json("charge_off_records.json")
```

Then add your sample data file to `app/data/charge_off_records.json`.

---

### Step 4 — Wire Up in `main.py`

Open `app/main.py` and make the following three changes:

**1. Import your new tools:**
```python
from app.tools.charge_off_tools import get_charge_off_status
```

**2. Load your procedure:**
```python
procedure = load_schema_json("charge_off_review_procedure.json")
```

**3. Pass your tools to the validation agent builder:**
```python
qc_validation_agent = build_qc_validation_agent(
    tools=[get_charge_off_status]
)
```

If your QC also needs a custom population tool, pass it to the data fetcher builder in the same way:
```python
data_fetcher_agent = build_data_fetcher_agent(
    tools=[get_my_population_batch]
)
```

Everything else in `main.py` — the orchestrator builder, decision agent builder, run loop, output rendering — stays the same.

---

### Step 5 — Add Sample Data and Test Cases

Add sample accounts to your data files that cover all the decision matrix branches: expected pass cases, expected fail cases, and any `manual_review` or edge cases. Mirror the structure of `app/data/test_case_catalog.json` to document the purpose of each sample account.

---

### Step 6 — Run and Verify

```bash
cd qc_strands_project
PYTHONPATH=$(pwd) ../.venv/bin/python3 -m app.main -v --cursor 0
```

Walk through the verbose output and confirm:
- Population batch loads the correct fields
- Each evidence collection step calls the right tool and returns the expected dict shape
- Each step decision correctly applies the rule from your `decision_matrix`
- The final decision correctly aggregates all step decisions using the policy you set
- `status` is `completed` with no `error` fields populated

Run across multiple cursors to exercise every decision matrix branch before treating the QC as validated.

---

### Checklist Summary

```
[ ] app/schemas/<new_qc>_procedure.json created and follows the structure
[ ] population_phase step has correct required_output_fields and evidence_tools
[ ] account_phase has paired evidence_collection + step_decision steps for each check
[ ] account_phase ends with a single final_decision step
[ ] depends_on is set correctly on every step
[ ] evaluation_rules decision_matrix covers all flag × evidence combinations
[ ] decision_policy aggregation policies set
[ ] app/tools/<new_qc>_tools.py created with @tool-decorated functions
[ ] Tool names in Python match names used in evidence_tools in the procedure
[ ] app/main.py updated: import, procedure load, tools passed to agent builders
[ ] Sample data added covering pass, fail, and edge-case accounts
[ ] End-to-end run produces correct verdicts across all test cursors
```

---

## Example Procedure — Internal Recovery Potential Settlements

This is the fully working procedure JSON for the settlement QC that ships with this repository. Use it as a concrete template when creating a procedure for a new QC. Replace all settlement-specific field names, tool names, rule IDs, and decision matrix values with those that match your new QC.

```json
{
  "qc_name": "settlement_review_qc",
  "procedure_name": "Internal Recovery Potential Settlements",
  "description": "Validates whether accounts flagged in the population as settled in full (SIF) have sufficient corroborating evidence in account tags and AR activity logs. Produces a step-level evidence decision and a final account-level QC verdict.",
  "unit_of_work": "account",
  "orchestration_mode": "dynamic",

  "agents": [
    {
      "role": "data_fetcher_agent",
      "purpose": "Retrieves structured population batches. Performs no QC judgment.",
      "tool_name": "fetch_structured_qc_data"
    },
    {
      "role": "qc_validation_agent",
      "purpose": "Gathers raw evidence for one account using registered evidence tools. Returns evidence only.",
      "tool_name": "collect_qc_evidence"
    },
    {
      "role": "qc_decision_agent",
      "purpose": "Evaluates evidence and rules to produce step-level or final-level QC decisions. Does not retrieve data.",
      "tool_name": "make_qc_decision"
    }
  ],

  "population_phase": {
    "description": "Retrieve the accounts to be reviewed. The orchestrator runs this phase once per batch before entering the account phase.",
    "steps": [
      {
        "step_id": "pop-1",
        "step_type": "data_retrieval",
        "title": "Load population batch",
        "objective": "Retrieve a paginated batch of accounts in scope for this QC run.",
        "preferred_agent": "data_fetcher_agent",
        "required_output_fields": ["account_number", "settlement_flag", "borrower", "co_borrower"],
        "evidence_tools": ["get_population_batch"],
        "depends_on": [],
        "evaluation_rule_ids": [],
        "decision_policy": null
      }
    ]
  },

  "account_phase": {
    "description": "For each account in the population batch, the orchestrator runs evidence collection, then step-level decisions, then the final account decision. Steps are driven by depends_on and step_type — the orchestrator must not hardcode a fixed sequence.",
    "iteration": "per_account",
    "steps": [
      {
        "step_id": "acct-1a",
        "step_type": "evidence_collection",
        "title": "Collect SIF tag evidence",
        "objective": "Gather SIF account tag evidence for the account.",
        "preferred_agent": "qc_validation_agent",
        "evidence_tools": ["get_account_tag_sif_presence"],
        "depends_on": ["pop-1"],
        "evaluation_rule_ids": [],
        "decision_policy": null
      },
      {
        "step_id": "acct-1b",
        "step_type": "step_decision",
        "title": "Evaluate SIF tag evidence",
        "objective": "Apply rule_sif_tag to the tag evidence collected in acct-1a. Return a step-level decision.",
        "preferred_agent": "qc_decision_agent",
        "evidence_tools": [],
        "depends_on": ["pop-1", "acct-1a"],
        "evaluation_rule_ids": ["rule_sif_tag"],
        "decision_policy": "any_fail_fails",
        "orchestration_hint": "Call make_qc_decision with decision_mode=step_decision. Pass account_context, evidence_bundle from acct-1a, and the rule subset for evaluation_rule_ids (rule_sif_tag only)."
      },
      {
        "step_id": "acct-2a",
        "step_type": "evidence_collection",
        "title": "Collect AR log settlement evidence",
        "objective": "Gather AR log evidence for the account.",
        "preferred_agent": "qc_validation_agent",
        "evidence_tools": ["get_arlog_settlement_evidence"],
        "depends_on": ["pop-1"],
        "evaluation_rule_ids": [],
        "decision_policy": null
      },
      {
        "step_id": "acct-2b",
        "step_type": "step_decision",
        "title": "Evaluate AR log settlement evidence",
        "objective": "Apply rule_arlog_direct and conditional rule_arlog_comment to the AR log evidence.",
        "preferred_agent": "qc_decision_agent",
        "evidence_tools": [],
        "depends_on": ["pop-1", "acct-2a"],
        "evaluation_rule_ids": ["rule_arlog_direct", "rule_arlog_comment"],
        "decision_policy": "any_fail_fails",
        "orchestration_hint": "Call make_qc_decision with decision_mode=step_decision. Pass account_context, evidence_bundle from acct-2a, and the rule subset (rule_arlog_direct + rule_arlog_comment). Note rule_arlog_comment is conditional_fallback — skip if settled_in_full_found is true."
      },
      {
        "step_id": "acct-3",
        "step_type": "final_decision",
        "title": "Final account QC verdict",
        "objective": "Aggregate step decisions from acct-1b and acct-2b into one final QC verdict.",
        "preferred_agent": "qc_decision_agent",
        "evidence_tools": [],
        "depends_on": ["acct-1b", "acct-2b"],
        "evaluation_rule_ids": ["rule_final_aggregation"],
        "decision_policy": "any_fail_fails_any_manual_review_manual_review",
        "orchestration_hint": "Call make_qc_decision with decision_mode=final_decision. Pass account_context, step_decisions=[acct-1b result, acct-2b result], and evaluation_rules for rule_final_aggregation."
      }
    ]
  },

  "evaluation_rules": [
    {
      "rule_id": "rule_sif_tag",
      "title": "SIF tag alignment",
      "description": "The account tag SIF presence must align with settlement_flag.",
      "evidence_check": "account_tag_sif_presence",
      "evidence_field": "sif_present",
      "applies_to_step": "acct-1b",
      "allowed_decisions": ["pass", "fail", "manual_review"],
      "decision_matrix": [
        {"account_context_field": "settlement_flag", "account_context_value": "Y", "evidence_value": true,  "decision": "pass", "note": "Flag=Y and SIF tag present — consistent"},
        {"account_context_field": "settlement_flag", "account_context_value": "Y", "evidence_value": false, "decision": "fail", "note": "Flag=Y but no SIF tag found — missing expected tag is a QC failure"},
        {"account_context_field": "settlement_flag", "account_context_value": "N", "evidence_value": true,  "decision": "fail", "note": "Flag=N but SIF tag found — direct contradiction"},
        {"account_context_field": "settlement_flag", "account_context_value": "N", "evidence_value": false, "decision": "pass", "note": "Flag=N and no SIF tag — consistent"}
      ]
    },
    {
      "rule_id": "rule_arlog_direct",
      "title": "AR log direct settlement record",
      "description": "If settlement_flag=Y, settled_in_full_found must be true to pass. If settlement_flag=N and settled_in_full_found=true, that is a contradiction.",
      "evidence_check": "arlog_settlement_evidence",
      "evidence_field": "settled_in_full_found",
      "applies_to_step": "acct-2b",
      "allowed_decisions": ["pass", "fail"],
      "decision_matrix": [
        {"account_context_field": "settlement_flag", "account_context_value": "Y", "evidence_value": true,  "decision": "pass", "note": "Flag=Y and SIF AR log row found — consistent"},
        {"account_context_field": "settlement_flag", "account_context_value": "Y", "evidence_value": false, "decision": "fail", "note": "Flag=Y but no SIF AR log row — missing expected evidence is a QC failure"},
        {"account_context_field": "settlement_flag", "account_context_value": "N", "evidence_value": true,  "decision": "fail", "note": "Flag=N but SIF AR log row found — direct contradiction"},
        {"account_context_field": "settlement_flag", "account_context_value": "N", "evidence_value": false, "decision": "pass", "note": "Flag=N and no SIF AR log row — consistent"}
      ]
    },
    {
      "rule_id": "rule_arlog_comment",
      "title": "AR log comment secondary check",
      "description": "When no direct settled_in_full rows exist, the latest AR log comment is reviewed for secondary settlement implication.",
      "evidence_check": "arlog_settlement_evidence",
      "evidence_field": "latest_comment_message",
      "applies_to_step": "acct-2b",
      "allowed_decisions": ["pass", "fail", "manual_review"],
      "rule_type": "conditional_fallback",
      "fallback_condition": "Only applies when settled_in_full_found is false. If settled_in_full_found is true, skip this rule entirely. When latest_comment_message is null, treat as does_not_imply_settlement.",
      "comment_implies_settlement_when": "The comment positively asserts the account was settled, paid in full, or fully resolved.",
      "comment_does_not_imply_settlement_when": [
        "The settlement keyword is negated — e.g. 'not final settlement', 'no settlement activity'",
        "The comment is ambiguous or pending — e.g. 'awaiting confirmation', 'under review'",
        "The comment merely references a settlement-related topic without confirming it"
      ],
      "decision_matrix": [
        {"account_context_field": "settlement_flag", "account_context_value": "Y", "evidence_value": "implies_settlement",         "decision": "pass", "note": "Flag=Y and comment implies settlement — supports the flag"},
        {"account_context_field": "settlement_flag", "account_context_value": "Y", "evidence_value": "does_not_imply_settlement", "decision": "fail", "note": "Flag=Y but AR log comment does not imply settlement — QC failure"},
        {"account_context_field": "settlement_flag", "account_context_value": "N", "evidence_value": "implies_settlement",         "decision": "fail", "note": "Flag=N but comment implies settlement — contradiction"},
        {"account_context_field": "settlement_flag", "account_context_value": "N", "evidence_value": "does_not_imply_settlement", "decision": "pass", "note": "Flag=N and comment does not imply settlement — consistent"}
      ]
    },
    {
      "rule_id": "rule_final_aggregation",
      "title": "Final verdict aggregation",
      "description": "Aggregates step-level decisions into one final account verdict. Apply the aggregation policy defined in the prompt.",
      "evidence_check": null,
      "evidence_field": null,
      "applies_to_step": "acct-3",
      "allowed_decisions": ["pass", "fail", "manual_review"]
    }
  ],

  "decision_policy": {
    "step_decisions_enabled": true,
    "final_decision_enabled": true,
    "dynamic_decision_invocation": true,
    "description": "The orchestrator may call the decision agent dynamically whenever sufficient evidence exists for a step.",
    "step_aggregation_policy": "any_fail_fails",
    "final_aggregation_policy": "any_fail_fails_any_manual_review_manual_review",
    "allowed_step_outcomes": ["pass", "fail", "manual_review", "error"],
    "allowed_final_outcomes": ["pass", "fail", "manual_review"]
  },

  "result_schema": {
    "description": "Defines the expected fields of each per-account QC result produced by the orchestrator.",
    "account_level_fields": [
      {"field": "account_number",        "type": "string", "description": "The account identifier from the population phase."},
      {"field": "account_context",       "type": "object", "description": "Full account context from the population output."},
      {"field": "step_outputs",          "type": "object", "description": "Keyed by step_id. Raw evidence bundle per evidence_collection step."},
      {"field": "step_decisions",        "type": "object", "description": "Keyed by step_id. Decision string per step_decision step."},
      {"field": "step_decision_reasons", "type": "object", "description": "Keyed by step_id. Reason string per step_decision step."},
      {"field": "final_decision",        "type": "string", "description": "Aggregated final verdict. Allowed: pass, fail, manual_review."},
      {"field": "final_decision_reason", "type": "string", "description": "Concise explanation of the final verdict."}
    ],
    "execution_status": {
      "field": "status",
      "allowed_values": ["completed", "error"],
      "note": "manual_review must only appear inside final_decision or step_decisions, never as execution status."
    },
    "error_schema": {
      "fields": [
        {"field": "type",    "description": "Short error class identifier."},
        {"field": "message", "description": "Human-readable explanation of what went wrong."},
        {"field": "step_id", "description": "The step_id at which the failure occurred, if known."}
      ]
    }
  },

  "checkpoint_scope": {
    "description": "Limits for local testing only. Not for production use.",
    "max_population_batches": 1,
    "max_accounts_to_process": 1,
    "selection_rule": "process the first account in the first batch only",
    "batch_size": 2
  }
}
```
