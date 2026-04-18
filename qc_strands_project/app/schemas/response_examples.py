"""Shared response models and placeholder schema notes for QC workflows."""

# These sample responses are for the first settlement QC example only.
# They act as placeholder schemas for initial validation.
# The system should be designed so that new tools and schemas can be added
# for other QC types.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QCProcedureStep(BaseModel):
    """One procedure step from a QC work instruction."""

    step_id: str
    title: str
    objective: str
    preferred_agent: str
    required_fields: list[str] = Field(default_factory=list)
    evidence_tools: list[str] = Field(default_factory=list)


class QCTask(BaseModel):
    """Top-level task received by the orchestrator."""

    qc_name: str
    procedure_name: str
    batch_id: str
    accounts: list[str]
    procedure_steps: list[QCProcedureStep]


class PopulationRecord(BaseModel):
    """Generic structured row returned by the data fetcher."""

    account_number: str
    fields: dict[str, Any]


class PopulationFetchResult(BaseModel):
    """Batch-level or account-level retrieval output."""

    qc_name: str
    step_id: str
    source_name: str
    level: str
    records: list[PopulationRecord]
    requested_fields: list[str]


class EvidenceItem(BaseModel):
    """Single evidence artifact returned for one account."""

    tool_name: str
    account_number: str
    evidence_type: str
    details: dict[str, Any]


class QCValidationResult(BaseModel):
    """Evidence-only response from the QC validation agent."""

    qc_name: str
    step_id: str
    account_number: str
    evidence: list[EvidenceItem]
    notes: list[str] = Field(default_factory=list)


class OrchestrationPlan(BaseModel):
    """Plan assembled from the QC procedure."""

    qc_name: str
    batch_id: str
    ordered_steps: list[dict[str, Any]]
    downstream_agents: list[str]


class ExecutionState(BaseModel):
    """Runtime state maintained by the orchestrator."""

    qc_name: str
    batch_id: str
    current_step_id: str | None = None
    current_account: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    progress_log: list[dict[str, Any]] = Field(default_factory=list)
