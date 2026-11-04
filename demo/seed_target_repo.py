"""Create (or repair) the throwaway target repo on your GitHub account.

    GITHUB_TOKEN=<token with repo scope> python -m demo.seed_target_repo --repo you/conductor-demo

Idempotent: every step checks what exists first, so a second run changes nothing. It refuses to
touch an existing repo unless that repo carries the throwaway marker in its description, so it
cannot damage a real project. Use a throwaway repo and a throwaway token.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from collections.abc import Callable

import httpx

from demo.target_files import DESCRIPTION, MAIN, PR1_BRANCH, PR1_FILES, PR2_BRANCH, PR2_FILES, PULLS

MARKER = "conductor-demo-throwaway"


class SeedError(Exception):
    pass


class Seeder:
    def __init__(self, client: httpx.Client, repo: str, say: Callable[[str], None] = print) -> None:
        self.c, self.repo, self.say = client, repo, say
        self.owner, self.name = repo.split("/", 1)

    def _ok(self, r: httpx.Response, *allowed: int) -> httpx.Response:
        if r.status_code not in (200, 201, *allowed):
            raise SeedError(f"GitHub said {r.status_code} for {r.request.url.path}: {r.text[:200]}")
        return r

    def ensure_repo(self) -> str:
        r = self.c.get(f"/repos/{self.repo}")
        if r.status_code == 404:
            body = {
                "name": self.name,
                "description": DESCRIPTION,
                "private": True,
                "auto_init": True,
            }
            r = self._ok(self.c.post("/user/repos", json=body))
            self.say(f"created {self.repo}")
        else:
            self._ok(r)
            if MARKER not in (r.json().get("description") or ""):
                raise SeedError(
                    f"{self.repo} exists but is not marked as a throwaway ({MARKER!r} missing "
                    "from its description); refusing to write to it."
                )
        return str(r.json().get("default_branch", "main"))

    def put_file(self, path: str, content: str, branch: str) -> bool:
        url = f"/repos/{self.repo}/contents/{path}"
        cur = self.c.get(url, params={"ref": branch})
        sha = None
        if cur.status_code == 200:
            existing = base64.b64decode(cur.json()["content"]).decode()
            if existing == content:
                return False
            sha = cur.json()["sha"]
        body = {
            "message": f"seed: {path}",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        self._ok(self.c.put(url, json=body))
        self.say(f"wrote {path} on {branch}")
        return True

    def ensure_branch(self, branch: str, base: str) -> None:
        if self.c.get(f"/repos/{self.repo}/git/ref/heads/{branch}").status_code == 200:
            return
        sha = self._ok(self.c.get(f"/repos/{self.repo}/git/ref/heads/{base}")).json()["object"][
            "sha"
        ]
        self._ok(
            self.c.post(
                f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}
            )
        )
        self.say(f"created branch {branch}")

    def ensure_pr(self, branch: str, title: str, body: str, base: str) -> int:
        found = self._ok(
            self.c.get(
                f"/repos/{self.repo}/pulls",
                params={"head": f"{self.owner}:{branch}", "state": "all"},
            )
        ).json()
        if found:
            return int(found[0]["number"])
        r = self._ok(
            self.c.post(
                f"/repos/{self.repo}/pulls",
                json={"title": title, "head": branch, "base": base, "body": body},
            )
        )
        self.say(f"opened PR #{r.json()['number']}: {title}")
        return int(r.json()["number"])

    def run(self) -> dict[str, int]:
        base = self.ensure_repo()
        for path, content in MAIN.items():
            self.put_file(path, content, base)
        numbers: dict[str, int] = {}
        branches = {PR1_BRANCH: PR1_FILES, PR2_BRANCH: PR2_FILES}
        for branch, title, body in PULLS:
            self.ensure_branch(branch, base)
            for path, content in branches[branch].items():
                self.put_file(path, content, branch)
            numbers[branch] = self.ensure_pr(branch, title, body, base)
        if sorted(numbers.values()) != [1, 2]:
            self.say(
                f"note: PRs are {numbers}; the demo expects #1 and #2, so use a fresh repo "
                "(or edit DEMO_PR1/DEMO_PR2)."
            )
        return numbers


def make_client(token: str, base_url: str | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=base_url or os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/name of the THROWAWAY repo")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Set GITHUB_TOKEN (repo scope, throwaway account or token).", file=sys.stderr)
        return 2
    try:
        numbers = Seeder(make_client(token), args.repo).run()
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"done. Set DEMO_REPO={args.repo}. PRs: {numbers}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
