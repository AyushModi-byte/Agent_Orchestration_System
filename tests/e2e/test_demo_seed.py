"""The GitHub seeder against an in-memory fake of the GitHub API: idempotent and cautious."""

from __future__ import annotations

import base64
import json
import re

import httpx
import pytest
from demo.seed_target_repo import Seeder, SeedError
from demo.target_files import DESCRIPTION, MAIN


class FakeGitHub:
    def __init__(self, existing: str | None = None) -> None:
        self.repo: dict[str, str] | None = (
            {"description": existing} if existing is not None else None
        )
        self.files: dict[tuple[str, str], str] = {}
        self.branches: dict[str, str] = {"main": "sha-main"}
        self.pulls: list[dict[str, object]] = []
        self.writes = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        body = json.loads(req.content) if req.content else {}
        if path == "/repos/me/demo" and m == "GET":
            if self.repo is None:
                return httpx.Response(404)
            return httpx.Response(200, json={**self.repo, "default_branch": "main"})
        if path == "/user/repos":
            self.repo = {"description": body["description"]}
            self.writes += 1
            return httpx.Response(201, json={"default_branch": "main"})
        if g := re.fullmatch(r"/repos/me/demo/contents/(.+)", path):
            f, branch = g.group(1), req.url.params.get("ref") or body.get("branch")
            if m == "GET":
                inherited = self.files.get((f, branch)) or self.files.get((f, "main"))
                if inherited is None:
                    return httpx.Response(404)
                return httpx.Response(
                    200, json={"sha": "s", "content": base64.b64encode(inherited.encode()).decode()}
                )
            self.files[(f, branch)] = base64.b64decode(body["content"]).decode()
            self.writes += 1
            return httpx.Response(201, json={})
        if g := re.fullmatch(r"/repos/me/demo/git/ref/heads/(.+)", path):
            sha = self.branches.get(g.group(1))
            return (
                httpx.Response(200, json={"object": {"sha": sha}}) if sha else httpx.Response(404)
            )
        if path == "/repos/me/demo/git/refs":
            self.branches[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            self.writes += 1
            return httpx.Response(201, json={})
        if path == "/repos/me/demo/pulls" and m == "GET":
            head = req.url.params["head"].split(":", 1)[1]
            return httpx.Response(200, json=[p for p in self.pulls if p["head"] == head])
        if path == "/repos/me/demo/pulls":
            pr = {"number": len(self.pulls) + 1, "head": body["head"]}
            self.pulls.append(pr)
            self.writes += 1
            return httpx.Response(201, json=pr)
        return httpx.Response(500, text=f"unexpected {m} {path}")


def seeder(gh: FakeGitHub) -> Seeder:
    client = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(gh))
    return Seeder(client, "me/demo", say=lambda _m: None)


def test_seeding_creates_the_repo_files_and_two_prs() -> None:
    gh = FakeGitHub()
    assert seeder(gh).run() == {"pr1-order-lookup": 1, "fix/sql-injection": 2}
    assert set(MAIN) <= {f for f, b in gh.files if b == "main"}
    assert "'%s'" in gh.files[("app/db.py", "main")]  # the planted SQL injection


def test_seeding_twice_changes_nothing() -> None:
    gh = FakeGitHub()
    seeder(gh).run()
    before = gh.writes
    assert seeder(gh).run() == {"pr1-order-lookup": 1, "fix/sql-injection": 2}
    assert gh.writes == before


def test_it_refuses_to_touch_a_repo_that_is_not_marked_throwaway() -> None:
    gh = FakeGitHub(existing="my real project")
    with pytest.raises(SeedError, match="throwaway"):
        seeder(gh).run()
    assert gh.writes == 0
    ok = FakeGitHub(existing=DESCRIPTION)
    seeder(ok).run()
    assert ok.writes > 0
