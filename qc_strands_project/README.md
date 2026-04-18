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
