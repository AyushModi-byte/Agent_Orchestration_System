"""ruff, bandit and pytest run inside the sandbox and are parsed into structured findings.
Agents only ever see Finding objects, never raw tool text, so evidence always has file+line."""

from __future__ import annotations

import json
from typing import Any

from defusedxml import ElementTree
from pydantic import BaseModel

from conductor.tools import sandbox
from conductor.tools.models import READERS, Finding, FindingsOutput, SnapshotInput
from conductor.tools.registry import tool

_JUNIT_MARK = "===JUNIT==="


class TestsOutput(BaseModel):
    __test__ = False  # not a pytest class

    passed: int
    failed: int
    errors: int
    findings: list[Finding]
    exit_code: int
    timed_out: bool = False


def _rel(path: str) -> str:
    for prefix in ("/work/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path


def parse_ruff(raw: str) -> list[Finding]:
    if not raw.strip():
        return []
    return [
        Finding(
            file=_rel(d["filename"]),
            line=d["location"]["row"],
            rule=d.get("code") or "ruff",
            # syntax errors and pyflakes hits are likely real bugs; style stays a warning
            severity="error" if (d.get("code") or "").startswith(("E9", "F")) else "warning",
            message=d["message"],
        )
        for d in json.loads(raw)
    ]


def parse_bandit(raw: str) -> list[Finding]:
    if not raw.strip():
        return []
    data: dict[str, Any] = json.loads(raw)
    return [
        Finding(
            file=_rel(r["filename"]),
            line=r["line_number"],
            rule=r["test_id"],
            severity=r["issue_severity"].lower(),
            message=r["issue_text"],
        )
        for r in data.get("results", [])
    ]


def parse_junit(xml: str) -> TestsOutput:
    root = ElementTree.fromstring(xml)
    passed = failed = errors = 0
    findings: list[Finding] = []
    for case in root.iter("testcase"):
        bad = case.find("failure")
        kind = "failure"
        if bad is None:
            bad = case.find("error")
            kind = "error"
        if bad is None:
            if case.find("skipped") is None:
                passed += 1
            continue
        if kind == "failure":
            failed += 1
        else:
            errors += 1
        classname = case.get("classname", "")
        file = case.get("file") or classname.replace(".", "/") + ".py"
        findings.append(
            Finding(
                file=_rel(file),
                line=int(case.get("line") or 0) + 1 if case.get("line") else 0,
                rule=f"test_{kind}:{case.get('name', '')}",
                severity="error",
                message=(bad.get("message") or "")[:500],
            )
        )
    return TestsOutput(passed=passed, failed=failed, errors=errors, findings=findings, exit_code=0)


@tool(
    name="run_ruff",
    description="Lint a repo snapshot with ruff in the sandbox.",
    input_schema=SnapshotInput,
    output_schema=FindingsOutput,
    allowed_agents={"code_analysis", "fix_writer", "reviewer", "mcp"},
    rate_limit=30,
)
async def run_ruff(args: SnapshotInput) -> FindingsOutput:
    res = await sandbox.run_in_sandbox(
        args.snapshot_dir,
        ["ruff", "check", "--output-format", "json", "--no-cache", "."],
        args.timeout,
    )
    return FindingsOutput(
        findings=parse_ruff(res.stdout), exit_code=res.exit_code, timed_out=res.timed_out
    )


@tool(
    name="run_bandit",
    description="Security-scan a repo snapshot with bandit in the sandbox.",
    input_schema=SnapshotInput,
    output_schema=FindingsOutput,
    allowed_agents={"security", "reviewer", "mcp"},
    rate_limit=30,
)
async def run_bandit(args: SnapshotInput) -> FindingsOutput:
    res = await sandbox.run_in_sandbox(
        args.snapshot_dir, ["bandit", "-r", ".", "-f", "json", "-q"], args.timeout
    )
    return FindingsOutput(
        findings=parse_bandit(res.stdout), exit_code=res.exit_code, timed_out=res.timed_out
    )


@tool(
    name="run_tests",
    description="Run the repo's pytest suite in the sandbox and return failures as findings.",
    input_schema=SnapshotInput,
    output_schema=TestsOutput,
    allowed_agents={"test_execution", "fix_writer", "reviewer", "mcp"},
    rate_limit=20,
)
async def run_tests(args: SnapshotInput) -> TestsOutput:
    script = (
        "pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml=/tmp/j.xml;"
        f" echo; echo {_JUNIT_MARK}; cat /tmp/j.xml"
    )
    res = await sandbox.run_in_sandbox(args.snapshot_dir, ["sh", "-c", script], args.timeout)
    if res.timed_out or _JUNIT_MARK not in res.stdout:
        return TestsOutput(
            passed=0,
            failed=0,
            errors=0,
            findings=[],
            exit_code=res.exit_code,
            timed_out=res.timed_out,
        )
    out = parse_junit(res.stdout.rsplit(_JUNIT_MARK, 1)[1].strip())
    return out.model_copy(update={"exit_code": res.exit_code})


__all__ = ["READERS", "run_bandit", "run_ruff", "run_tests"]
