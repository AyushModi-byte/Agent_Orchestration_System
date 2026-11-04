"""GitHub tools against recorded fixtures (respx); no network."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from conductor.tools import github  # noqa: F401
from conductor.tools.call_log import SqliteToolCallStore
from conductor.tools.registry import PermissionDenied, ToolRegistry, default_registry

BASE = "https://api.github.com"
PR = {
    "number": 7,
    "title": "Add login",
    "body": None,
    "state": "open",
    "user": {"login": "dev"},
    "head": {"ref": "feat", "sha": "abc"},
    "base": {"ref": "main"},
    "labels": [{"name": "production"}],
}


@pytest.fixture
def reg(monkeypatch: pytest.MonkeyPatch) -> ToolRegistry:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("DRY_RUN", raising=False)
    default_registry.store = SqliteToolCallStore()
    return default_registry


@respx.mock
async def test_get_pr(reg: ToolRegistry) -> None:
    respx.get(f"{BASE}/repos/o/r/pulls/7").mock(return_value=httpx.Response(200, json=PR))
    out = await reg.invoke("reviewer", "get_pr", {"repo": "o/r", "number": 7})
    assert out.model_dump()["labels"] == ["production"]
    assert out.model_dump()["body"] == ""


@respx.mock
async def test_get_pr_diff_and_files(reg: ToolRegistry) -> None:
    respx.get(f"{BASE}/repos/o/r/pulls/7/files").mock(
        return_value=httpx.Response(
            200,
            json=[{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 0}],
        )
    )
    respx.get(f"{BASE}/repos/o/r/pulls/7").mock(
        return_value=httpx.Response(200, text="diff --git a/a.py b/a.py")
    )
    files = await reg.invoke("security", "list_pr_files", {"repo": "o/r", "number": 7})
    assert files.model_dump()["files"][0]["filename"] == "a.py"
    diff = await reg.invoke("security", "get_pr_diff", {"repo": "o/r", "number": 7})
    assert diff.model_dump()["diff"].startswith("diff --git")


@respx.mock
async def test_get_file_decodes(reg: ToolRegistry) -> None:
    respx.get(f"{BASE}/repos/o/r/contents/a.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "a.py",
                "sha": "s1",
                "content": base64.b64encode(b"print(1)\n").decode(),
            },
        )
    )
    out = await reg.invoke(
        "code_analysis", "get_file", {"repo": "o/r", "path": "a.py", "ref": "feat"}
    )
    assert out.model_dump()["content"] == "print(1)\n"


@respx.mock
async def test_writes_are_dry_run_by_default(reg: ToolRegistry) -> None:
    route = respx.post(f"{BASE}/repos/o/r/pulls/7/reviews")
    out = await reg.invoke(
        "reviewer", "post_review_comment", {"repo": "o/r", "number": 7, "body": "hi"}
    )
    assert out.model_dump()["dry_run"] is True
    assert not route.called


@respx.mock
async def test_post_review_live_when_dry_run_off(
    reg: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRY_RUN", "0")
    route = respx.post(f"{BASE}/repos/o/r/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"html_url": "http://x"})
    )
    out = await reg.invoke(
        "reviewer",
        "post_review_comment",
        {
            "repo": "o/r",
            "number": 7,
            "body": "hi",
            "comments": [{"path": "a.py", "line": 3, "body": "b"}],
        },
    )
    assert route.called and out.model_dump()["url"] == "http://x"


async def test_push_fix_permissions_and_branch_guard(reg: ToolRegistry) -> None:
    args = {"repo": "o/r", "branch": "feat", "path": "a.py", "content": "x", "message": "m"}
    with pytest.raises(PermissionDenied):
        await reg.invoke("reviewer", "push_fix_commit", args)
    with pytest.raises(ValueError, match="default branch"):
        await reg.invoke("fix_writer", "push_fix_commit", {**args, "branch": "main"})
    out = await reg.invoke("fix_writer", "push_fix_commit", args)
    assert out.model_dump()["dry_run"] is True
