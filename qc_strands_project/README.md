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
- `app/schemas/sample_procedure.json` contains the sample procedure used by the local demo
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

The procedure must follow the same structure as `app/schemas/sample_procedure.json`. The required top-level sections are:

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
