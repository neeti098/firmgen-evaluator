from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


CriterionId = Literal[
    "problem_relevance",
    "functionality_reliability",
    "firmgen_usage",
    "technical_quality",
    "innovation_product_thinking",
    "scalability_robustness",
    "demonstration_documentation",
]


CRITERIA: dict[str, tuple[str, float]] = {
    "problem_relevance": ("Problem Relevance & Product Value", 20.0),
    "functionality_reliability": ("Functionality & Reliability", 25.0),
    "firmgen_usage": ("Effective Use of FirmGen", 20.0),
    "technical_quality": ("Technical Quality & Engineering", 15.0),
    "innovation_product_thinking": ("Innovation & Product Thinking", 10.0),
    "scalability_robustness": ("Scalability & Robustness", 5.0),
    "demonstration_documentation": ("Demonstration & Documentation", 5.0),
}


class CriterionScore(BaseModel):
    criterion_id: CriterionId
    score: float
    confidence: float
    rationale: str
    evidence: list[str]
    missing_evidence: list[str]


class FirmGenEvidence(BaseModel):
    usage_detected: bool
    confidence: float
    task_list_state: Literal[
        "substantially_progressed",
        "partially_progressed",
        "visible_but_unreadable",
        "not_visible",
    ]
    doxygen_or_desktop_visible: bool
    evidence_sources: list[str]
    rationale: str


class ModelEvaluation(BaseModel):
    project_title: str
    project_summary: str
    observed_demo: str
    criteria: list[CriterionScore]
    firmgen_evidence: FirmGenEvidence
    manual_review_recommended: bool
    review_reasons: list[str]


class FinalEvaluation(ModelEvaluation):
    total_score: float = Field(ge=0, le=100)
    overall_confidence: float = Field(ge=0, le=1)
    model_used: str
    adjudicated: bool = False
    caps_applied: list[str] = Field(default_factory=list)
    firmware_eligible: bool = True
    firmware_evidence: list[str] = Field(default_factory=list)


class RepositorySubmission(BaseModel):
    repo_url: str
    team_id: str = ""
    force: bool = False


class JobSummary(BaseModel):
    id: str
    team_id: str
    repo_url: str
    status: str
    progress: str
    project_title: str = ""
    commit_sha: str = ""
    total_score: float | None = None
    overall_confidence: float | None = None
    manual_review_recommended: bool = False
    error: str = ""
    created_at: datetime
    updated_at: datetime
