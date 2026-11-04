"""Models and agent-permission sets shared by tool modules."""

from __future__ import annotations

from pydantic import BaseModel

READERS = {
    "supervisor",
    "code_analysis",
    "security",
    "test_execution",
    "fix_writer",
    "reviewer",
    "mcp",
}


class Finding(BaseModel):
    file: str
    line: int
    rule: str
    severity: str  # error | warning | info | low | medium | high
    message: str


class FindingsOutput(BaseModel):
    findings: list[Finding]
    exit_code: int
    timed_out: bool = False


class SnapshotInput(BaseModel):
    snapshot_dir: str
    timeout: int = 60
