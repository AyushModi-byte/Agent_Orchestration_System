from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from conductor.tools import linters, sandbox
from conductor.tools.deps import audit_requirements, parse_requirements
from conductor.tools.registry import ToolRegistry, default_registry

RUFF = json.dumps(
    [
        {
            "code": "F401",
            "filename": "/work/a.py",
            "location": {"row": 1, "column": 1},
            "message": "unused import",
        },
        {
            "code": "E501",
            "filename": "/work/pkg/b.py",
            "location": {"row": 9, "column": 1},
            "message": "too long",
        },
    ]
)
BANDIT = json.dumps(
    {
        "results": [
            {
                "filename": "./a.py",
                "line_number": 4,
                "test_id": "B602",
                "issue_severity": "HIGH",
                "issue_text": "shell=True",
            }
        ]
    }
)
JUNIT = (
    '<testsuites><testsuite><testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py" line="1"/>'
    '<testcase classname="tests.test_a" name="test_bad" file="tests/test_a.py" line="4">'
    '<failure message="assert 1 == 2">tb</failure></testcase></testsuite></testsuites>'
)


def test_parse_ruff_structured() -> None:
    f = linters.parse_ruff(RUFF)
    assert (f[0].file, f[0].line, f[0].rule, f[0].severity) == ("a.py", 1, "F401", "error")
    assert (f[1].file, f[1].severity) == ("pkg/b.py", "warning")


def test_parse_bandit_structured() -> None:
    (f,) = linters.parse_bandit(BANDIT)
    assert (f.file, f.line, f.rule, f.severity) == ("a.py", 4, "B602", "high")


def test_parse_junit_failures_become_findings() -> None:
    out = linters.parse_junit(JUNIT)
    assert (out.passed, out.failed) == (1, 1)
    assert out.findings[0].file == "tests/test_a.py"
    assert out.findings[0].message == "assert 1 == 2"


async def test_tools_via_registry_with_fake_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(snapshot_dir, cmd, timeout=60, image=""):  # type: ignore[no-untyped-def]
        if cmd[0] == "ruff":
            return sandbox.SandboxResult(exit_code=1, stdout=RUFF, stderr="")
        if cmd[0] == "bandit":
            return sandbox.SandboxResult(exit_code=1, stdout=BANDIT, stderr="")
        return sandbox.SandboxResult(exit_code=1, stdout="x\n===JUNIT===\n" + JUNIT, stderr="")

    monkeypatch.setattr(sandbox, "run_in_sandbox", fake_run)
    reg: ToolRegistry = default_registry
    r = await reg.invoke("code_analysis", "run_ruff", {"snapshot_dir": "."})
    assert len(r.model_dump()["findings"]) == 2
    b = await reg.invoke("security", "run_bandit", {"snapshot_dir": "."})
    assert b.model_dump()["findings"][0]["rule"] == "B602"
    t = await reg.invoke("test_execution", "run_tests", {"snapshot_dir": "."})
    assert t.model_dump()["failed"] == 1 and t.model_dump()["exit_code"] == 1


def test_parse_requirements_pins_only() -> None:
    text = "requests==2.0.0  # old\nflask>=2\n# c\nurllib3==1.25.0\n"
    assert parse_requirements(text) == [("requests", "2.0.0", 1), ("urllib3", "1.25.0", 4)]


@respx.mock
async def test_audit_requirements_osv(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("requests==2.0.0\nflask==3.0.0\n")
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [{"vulns": [{"id": "GHSA-xxxx"}]}, {}]})
    )
    out = await audit_requirements(str(req))
    assert out.checked == 2
    assert [(f.file, f.line, f.rule) for f in out.findings] == [
        ("requirements.txt", 1, "GHSA-xxxx")
    ]


async def test_mcp_server_exposes_only_mcp_permitted_tools() -> None:
    from conductor.tools.mcp_server import build_server

    names = {t.name for t in await build_server().list_tools()}
    assert {"get_pr", "run_ruff", "run_bandit", "run_tests", "audit_requirements"} <= names
    assert "push_fix_commit" not in names and "post_review_comment" not in names
