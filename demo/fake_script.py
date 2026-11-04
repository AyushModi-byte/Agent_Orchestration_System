"""Scripted demo scenario for CONDUCTOR_FAKE_LLM=1: the whole product with zero quota.

The story (PR 2 of acme/shop): code analysis and security run in parallel with the test run, the
fix writer's first patch breaks a test so the reviewer's evidence check rejects it, memory recalls
the lesson from PR 1, the second patch passes, and pushing it pauses for a human.

Everything here is stateless: each answer is computed from the messages it is given, so replays,
re-runs and several tasks at once behave identically.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from conductor.agents.prompts_loader import load_prompt
from conductor.llm.types import Message
from conductor.tools.registry import ToolRegistry

REPO = "acme/shop"
USER = "demo"

PLAN = {
    "steps": [
        {
            "id": "s1",
            "description": "Lint the changed files and read the diff",
            "specialist": "code_analysis",
            "depends_on": [],
            "complexity": "low",
        },
        {
            "id": "s2",
            "description": "Scan for injection, hard-coded secrets and vulnerable dependencies",
            "specialist": "security",
            "depends_on": [],
            "complexity": "medium",
        },
        {
            "id": "s3",
            "description": "Run the test suite on the PR head",
            "specialist": "test_execution",
            "depends_on": [],
            "complexity": "low",
        },
        {
            "id": "s4",
            "description": "Propose a parameterised-query fix for the SQL injection",
            "specialist": "fix_writer",
            "depends_on": ["s2", "s3"],
            "complexity": "high",
        },
    ],
    "confidence": 0.86,
    "rationale": "Independent checks first, then a fix that depends on security and test results.",
}

SEED_MEMORIES: list[tuple[str, str, str]] = [
    (
        "task_memories",
        "worked",
        "Review PR #1 on acme/shop. Worked: parameterised queries with cursor.execute(sql, params) "
        "fixed the SQL injection and kept test_login green",
    ),
    (
        "task_memories",
        "failed",
        "Review PR #1 on acme/shop. Failed: wrapping user input in str() did not stop the "
        "injection; the tests passed but bandit B608 still fired",
    ),
    (
        "task_memories",
        "decision",
        "Review PR on acme/shop: approved push of a SQL injection fix because tests passed "
        "and the diff touched one file",
    ),
    ("user_prefs", "preference", "Prefers small, single-file fixes with a one-line commit message"),
    ("repo_facts", "fact", "acme/shop keeps all database access in app/db.py and tests in tests/"),
]

RUFF = {
    "findings": [
        {"file": "app/utils.py", "line": 3, "rule": "F401", "message": "os imported but unused"},
        {"file": "app/db.py", "line": 27, "rule": "E501", "message": "line too long (112 > 100)"},
    ]
}
BANDIT = {
    "findings": [
        {
            "file": "app/db.py",
            "line": 42,
            "rule": "B608",
            "severity": "high",
            "message": "SQL built from string formatting",
        },
        {
            "file": "app/config.py",
            "line": 8,
            "rule": "B105",
            "severity": "medium",
            "message": "hard-coded password string",
        },
    ]
}
AUDIT = {
    "vulnerabilities": [
        {"file": "requirements.txt", "line": 3, "package": "jinja2==3.1.2", "id": "CVE-2024-22195"}
    ]
}
FIX_V1 = "cursor.execute('SELECT * FROM users WHERE name = ' + str(name))"
FIX_V2 = "cursor.execute('SELECT * FROM users WHERE name = %s', (name,))"


class AnyIn(BaseModel):
    model_config = {"extra": "allow"}


class AnyOut(BaseModel):
    model_config = {"extra": "allow"}


def demo_registry() -> ToolRegistry:
    """Canned tool results shaped like the real tools'. Writes are dry runs by construction."""
    reg = ToolRegistry()

    def add(name: str, agents: set[str], out: Any) -> None:
        @reg.tool(
            name=name,
            description=name,
            input_schema=AnyIn,
            output_schema=AnyOut,
            allowed_agents=agents,
        )
        async def _tool(args: Any) -> dict[str, Any]:
            return out(args.model_dump()) if callable(out) else dict(out)

    add(
        "list_pr_files",
        {"code_analysis"},
        {"files": [{"file": "app/db.py", "line": 1}, {"file": "app/utils.py", "line": 1}]},
    )
    add("get_file", {"code_analysis", "security", "fix_writer"}, {"content": "x = 1\n", "lines": 1})
    add("get_pr_diff", {"code_analysis"}, {"diff": '+ query = f"SELECT ... {name}"'})
    add("run_ruff", {"code_analysis", "fix_writer"}, RUFF)
    add("run_bandit", {"security"}, BANDIT)
    add("audit_requirements", {"security"}, AUDIT)

    def tests(args: dict[str, Any]) -> dict[str, Any]:
        # the first fix attempt breaks test_login; the second one does not
        broken = args.get("patch") == "v1"
        return {"passed": 40 if broken else 41, "failed": 1 if broken else 0, "errors": 0}

    add("run_tests", {"test_execution", "fix_writer"}, tests)
    add("post_review_comment", {"supervisor", "reviewer"}, {"dry_run": True})
    add("push_fix_commit", {"fix_writer"}, {"dry_run": True})
    return reg


def _finding(file: str, line: int, rule: str, severity: str, evidence: str) -> dict[str, Any]:
    return {"file": file, "line": line, "rule": rule, "severity": severity, "evidence": evidence}


def _tool(name: str, **args: Any) -> str:
    return json.dumps({"action": "tool", "tool": name, "args": args})


def _final(output: dict[str, Any], findings: list[dict[str, Any]], conf: float = 0.85) -> str:
    return json.dumps(
        {"action": "final", "output": output, "findings": findings, "confidence": conf}
    )


class DemoScript:
    """FakeLLM responder: (role, messages) -> text."""

    def __call__(self, role: str, messages: list[Message]) -> str:
        if role == "planner":
            return self._planner(messages)
        if role == "specialist":
            return self._specialist(messages)
        if role == "reviewer":
            return json.dumps({"approved": True, "score": 0.92, "issues": [], "feedback": ""})
        return self._cheap(messages)

    # -- supervisor -----------------------------------------------------------------------
    def _planner(self, messages: list[Message]) -> str:
        try:
            last = json.loads(messages[-1]["content"])
        except ValueError:  # re-ask after an invalid plan
            return json.dumps(PLAN)
        if "request" in last:
            memories = last.get("memories") or []
            plan = dict(PLAN)
            if memories:
                plan["rationale"] = (
                    f"{PLAN['rationale']} Recalled {len(memories)} earlier lesson(s) from PR 1: "
                    "use parameterised queries, not str() wrapping."
                )
            return json.dumps(plan)
        found = last.get("findings", [])
        return json.dumps(
            {
                "summary": f"{len(found)} verified issue(s) in acme/shop: SQL built from "
                "strings in app/db.py (high), a hard-coded password in app/config.py, and a "
                "vulnerable jinja2 pin. A parameterised-query fix passes all 41 tests."
            }
        )

    # -- specialists ----------------------------------------------------------------------
    def _specialist(self, messages: list[Message]) -> str:
        first = json.loads(messages[1]["content"])
        sub = first["subtask"]["specialist"]
        retry = bool(first.get("feedback") or first.get("hint"))
        seen = sum(1 for m in messages if m["role"] == "user" and "observation" in m["content"])
        if sub == "code_analysis":
            if seen == 0:
                return _tool("list_pr_files", repo=REPO, number=2)
            if seen == 1:
                return _tool("run_ruff", paths=["app/"])
            return _final(
                {"note": "2 style issues, no logic problems"},
                [_finding("app/utils.py", 3, "F401", "low", "ruff F401: os imported but unused")],
                0.8,
            )
        if sub == "security":
            if seen == 0:
                return _tool("run_bandit", target="app/")
            if seen == 1:
                return _tool("audit_requirements", path="requirements.txt")
            return _final(
                {"note": "injection, secret and CVE found"},
                [
                    _finding("app/db.py", 42, "B608", "high", "bandit B608: SQL built by string"),
                    _finding(
                        "app/config.py", 8, "B105", "medium", "bandit B105: hard-coded secret"
                    ),
                    _finding("requirements.txt", 3, "CVE-2024-22195", "medium", "jinja2 3.1.2 pin"),
                ],
                0.9,
            )
        if sub == "test_execution":
            if seen == 0:
                return _tool("run_tests", path="tests/")
            return _final({"note": "41 tests pass on the PR head"}, [], 0.9)
        # fix_writer: v1 is the naive patch the memory warns about, v2 follows the PR 1 lesson
        patch = "v2" if retry else "v1"
        if seen == 0:
            return _tool("run_tests", path="tests/", patch=patch)
        content = FIX_V2 if retry else FIX_V1
        return _final(
            {
                "note": "parameterise the user lookup",
                "fixes": [
                    {
                        "path": "app/db.py",
                        "content": content,
                        "message": "fix: parameterise user lookup",
                        "reasoning": "Parameterised queries stop injection and keep test_login "
                        "green; the same pattern fixed PR 1."
                        if retry
                        else "Wrap the input in str() before concatenating.",
                    }
                ],
            },
            [],
            0.8,
        )

    # -- cheap role: memory extraction and approval chat ------------------------------------
    def _cheap(self, messages: list[Message]) -> str:
        system = messages[0]["content"]
        if system == load_prompt("approval_chat"):
            return self._chat(messages)
        if system == load_prompt("memory_merge"):
            texts = json.loads(messages[-1]["content"]).get("memories", [])
            return json.dumps({"text": "; ".join(texts)[:400]})
        return json.dumps(
            {
                "what_asked": "Review PR #2 on acme/shop",
                "plan_summary": "lint, security scan and tests in parallel, then a parameterised "
                "query fix",
                "worked": ["parameterised queries fixed the injection and kept all tests green"],
                "failed": ["first fix wrapped input in str() and broke test_login"],
                "tools_used": ["run_ruff", "run_bandit", "run_tests"],
                "repo_facts": ["acme/shop pins jinja2 3.1.2, which has CVE-2024-22195"],
                "preferences": [],
            }
        )

    def _chat(self, messages: list[Message]) -> str:
        ctx = json.loads(messages[1]["content"].split("\n", 1)[1])
        q = messages[-1]["content"].lower()
        prop = ctx.get("proposed_action") or {}
        mems = ctx.get("memories") or []
        if "why" in q or "reason" in q:
            return prop.get("reasoning") or "The plan follows the supervisor's rationale."
        if "test" in q:
            done = ", ".join(s["specialist"] for s in ctx.get("completed_steps", []))
            return f"Approved steps so far: {done or 'none'}. The final test run had 0 failures."
        if "memor" in q or "before" in q or "past" in q:
            return "Recalled: " + (" | ".join(mems) if mems else "nothing relevant.")
        if "risk" in q:
            return "Low: a one-file change with a passing test run. Nothing is merged or deployed."
        return (
            f"This is a {ctx.get('kind')} approval at level {ctx.get('level')}. "
            f"Proposed: {prop.get('summary') or 'the plan above'}."
        )
