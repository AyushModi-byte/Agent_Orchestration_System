"""Pydantic contracts between agents. Everything crossing an agent boundary is one of these."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

SPECIALISTS = ("code_analysis", "security", "test_execution", "fix_writer")
Specialist = Literal["code_analysis", "security", "test_execution", "fix_writer"]


class Subtask(BaseModel):
    id: str
    description: str
    specialist: Specialist
    depends_on: list[str] = []
    required_inputs: list[str] = []
    expected_output_format: str = ""
    complexity: Literal["low", "medium", "high"] = "medium"


class ExecutionPlan(BaseModel):
    steps: list[Subtask]
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


class Finding(BaseModel):
    file: str
    line: int
    rule: str
    severity: str
    evidence: str


class ToolTrace(BaseModel):
    """What a tool call actually returned, kept so the reviewer can check claims against it."""

    tool: str
    args: dict[str, Any] = {}
    ok: bool
    summary: str = ""
    locations: list[tuple[str, int]] = []  # (file, line) pairs present in the tool output
    metrics: dict[str, int] = {}  # e.g. passed/failed/errors/lines


class SpecialistResult(BaseModel):
    subtask_id: str
    output: dict[str, Any] = {}
    findings: list[Finding] = []
    tool_summary: list[ToolTrace] = []
    confidence: float = Field(default=0.5, ge=0, le=1)
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)  # ties a verdict to one run


class ReviewJudgement(BaseModel):
    """What the reviewer LLM returns; ids are filled in by code, never trusted from the model."""

    approved: bool
    score: float = Field(ge=0, le=1)
    issues: list[str] = []
    feedback: str = ""


class ReviewVerdict(ReviewJudgement):
    subtask_id: str
    run_id: str = ""


class ReviewReport(BaseModel):
    summary: str
    findings: list[Finding] = []
    fixes: list[dict[str, Any]] = []


class StepDecision(BaseModel):
    """One turn of a specialist loop: call a tool, or finish."""

    action: Literal["tool", "final"]
    tool: str | None = None
    args: dict[str, Any] = {}
    output: dict[str, Any] = {}
    findings: list[Finding] = []
    confidence: float = Field(default=0.5, ge=0, le=1)
