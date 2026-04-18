"""Shared response models and example payloads for QC workflows."""

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


FIRST_QC_EXAMPLE = QCTask(
    qc_name="settlement_review_qc",
    procedure_name="Settlement Review Procedure v1",
    batch_id="batch-001",
    accounts=["100001", "100002"],
    procedure_steps=[
        QCProcedureStep(
            step_id="step-1",
            title="Load population context",
            objective="Retrieve only the account context needed for settlement review.",
            preferred_agent="data_fetcher_agent",
            required_fields=["account_number", "settlement_flag", "borrower", "co_borrower"],
        ),
        QCProcedureStep(
            step_id="step-2",
            title="Collect evidence",
            objective="Gather account-level evidence for settlement-related checks.",
            preferred_agent="qc_validation_agent",
            evidence_tools=["get_account_tag_sif_presence", "get_arlog_settlement_evidence"],
        ),
    ],
)
