"""GitHub tools. Writes honour DRY_RUN (default on). There is deliberately no merge or deploy
tool: merging is human-only (CLAUDE.md hard rule)."""

from __future__ import annotations

import base64
import os

import httpx
from pydantic import BaseModel

from conductor.tools.models import READERS
from conductor.tools.registry import tool


def _client() -> httpx.AsyncClient:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        headers=headers,
        timeout=30,
    )


def dry_run() -> bool:
    """Writes are simulated unless DRY_RUN=0 is set explicitly."""
    return os.environ.get("DRY_RUN", "1") != "0"


class PRRef(BaseModel):
    repo: str  # "owner/name"
    number: int


class PRInfo(BaseModel):
    number: int
    title: str
    body: str
    state: str
    author: str
    head_ref: str
    head_sha: str
    base_ref: str
    labels: list[str]


class DiffOutput(BaseModel):
    diff: str


class PRFile(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None


class PRFiles(BaseModel):
    files: list[PRFile]


class FileRef(BaseModel):
    repo: str
    path: str
    ref: str


class FileContent(BaseModel):
    path: str
    sha: str
    content: str


class LineComment(BaseModel):
    path: str
    line: int
    body: str


class ReviewInput(BaseModel):
    repo: str
    number: int
    body: str
    comments: list[LineComment] = []


class WriteResult(BaseModel):
    dry_run: bool
    url: str | None = None


class PushFixInput(BaseModel):
    repo: str
    branch: str
    path: str
    content: str
    message: str


@tool(
    name="get_pr",
    description="Fetch pull request metadata.",
    input_schema=PRRef,
    output_schema=PRInfo,
    allowed_agents=READERS,
    rate_limit=60,
)
async def get_pr(args: PRRef) -> PRInfo:
    async with _client() as c:
        r = await c.get(f"/repos/{args.repo}/pulls/{args.number}")
        r.raise_for_status()
        d = r.json()
    return PRInfo(
        number=d["number"],
        title=d["title"],
        body=d.get("body") or "",
        state=d["state"],
        author=d["user"]["login"],
        head_ref=d["head"]["ref"],
        head_sha=d["head"]["sha"],
        base_ref=d["base"]["ref"],
        labels=[lbl["name"] for lbl in d.get("labels", [])],
    )


@tool(
    name="get_pr_diff",
    description="Fetch the unified diff of a pull request.",
    input_schema=PRRef,
    output_schema=DiffOutput,
    allowed_agents=READERS,
    rate_limit=60,
)
async def get_pr_diff(args: PRRef) -> DiffOutput:
    async with _client() as c:
        r = await c.get(
            f"/repos/{args.repo}/pulls/{args.number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        r.raise_for_status()
        return DiffOutput(diff=r.text)


@tool(
    name="list_pr_files",
    description="List files changed in a pull request.",
    input_schema=PRRef,
    output_schema=PRFiles,
    allowed_agents=READERS,
    rate_limit=60,
)
async def list_pr_files(args: PRRef) -> PRFiles:
    async with _client() as c:
        r = await c.get(f"/repos/{args.repo}/pulls/{args.number}/files", params={"per_page": 100})
        r.raise_for_status()
        return PRFiles(files=[PRFile.model_validate(f) for f in r.json()])


@tool(
    name="get_file",
    description="Fetch a file's decoded contents at a git ref.",
    input_schema=FileRef,
    output_schema=FileContent,
    allowed_agents=READERS,
    rate_limit=120,
)
async def get_file(args: FileRef) -> FileContent:
    async with _client() as c:
        r = await c.get(f"/repos/{args.repo}/contents/{args.path}", params={"ref": args.ref})
        r.raise_for_status()
        d = r.json()
    return FileContent(
        path=d["path"],
        sha=d["sha"],
        content=base64.b64decode(d["content"]).decode("utf-8", errors="replace"),
    )


@tool(
    name="post_review_comment",
    description="Post a review (summary plus optional line comments) on a pull request.",
    input_schema=ReviewInput,
    output_schema=WriteResult,
    allowed_agents={"supervisor", "reviewer"},
    rate_limit=10,
)
async def post_review_comment(args: ReviewInput) -> WriteResult:
    if dry_run():
        return WriteResult(dry_run=True)
    payload = {
        "body": args.body,
        "event": "COMMENT",
        "comments": [c.model_dump() for c in args.comments],
    }
    async with _client() as c:
        r = await c.post(f"/repos/{args.repo}/pulls/{args.number}/reviews", json=payload)
        r.raise_for_status()
        return WriteResult(dry_run=False, url=r.json().get("html_url"))


@tool(
    name="push_fix_commit",
    description="Commit a single file change to a non-default branch (never merges).",
    input_schema=PushFixInput,
    output_schema=WriteResult,
    allowed_agents={"fix_writer"},
    rate_limit=10,
)
async def push_fix_commit(args: PushFixInput) -> WriteResult:
    if args.branch in {"main", "master"}:
        raise ValueError("refusing to push directly to a default branch")
    if dry_run():
        return WriteResult(dry_run=True)
    async with _client() as c:
        cur = await c.get(f"/repos/{args.repo}/contents/{args.path}", params={"ref": args.branch})
        sha = cur.json().get("sha") if cur.status_code == 200 else None
        body = {
            "message": args.message,
            "branch": args.branch,
            "content": base64.b64encode(args.content.encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = await c.put(f"/repos/{args.repo}/contents/{args.path}", json=body)
        r.raise_for_status()
        return WriteResult(dry_run=False, url=r.json()["commit"].get("html_url"))
