"""Shared test doubles: a fake tool registry and a behaviour-driven FakeLLM responder."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from conductor.llm.fake import FakeLLM
from conductor.llm.types import Message
from conductor.tools.registry import ToolRegistry


class AnyIn(BaseModel):
    model_config = {"extra": "allow"}


class AnyOut(BaseModel):
    model_config = {"extra": "allow"}


def make_registry(barrier: int = 0) -> ToolRegistry:
    """Registers fake versions of the real tool names. With barrier=N, run_ruff only returns once
    N callers are inside it at the same time, which proves they ran concurrently."""
    reg = ToolRegistry()
    state = {"inside": 0}
    event = asyncio.Event() if barrier else None

    @reg.tool(
        name="run_ruff",
        description="lint",
        input_schema=AnyIn,
        output_schema=AnyOut,
        allowed_agents={"code_analysis", "fix_writer"},
    )
    async def run_ruff(args: Any) -> dict[str, Any]:
        if event is not None:
            state["inside"] += 1
            if state["inside"] >= barrier:
                event.set()
            await asyncio.wait_for(event.wait(), timeout=3)
        return {"findings": [{"file": "a.py", "line": 3, "rule": "F401", "message": "unused"}]}

    @reg.tool(
        name="run_bandit",
        description="sec",
        input_schema=AnyIn,
        output_schema=AnyOut,
        allowed_agents={"security"},
    )
    async def run_bandit(args: Any) -> dict[str, Any]:
        if event is not None:
            state["inside"] += 1
            if state["inside"] >= barrier:
                event.set()
            await asyncio.wait_for(event.wait(), timeout=3)
        return {"findings": [{"file": "b.py", "line": 9, "rule": "B602", "message": "shell"}]}

    @reg.tool(
        name="run_tests",
        description="tests",
        input_schema=AnyIn,
        output_schema=AnyOut,
        allowed_agents={"test_execution", "fix_writer"},
    )
    async def run_tests(args: Any) -> dict[str, Any]:
        return {"passed": 3, "failed": 0, "errors": 0, "findings": []}

    reg.calls = []  # type: ignore[attr-defined]

    @reg.tool(
        name="post_review_comment",
        description="comment",
        input_schema=AnyIn,
        output_schema=AnyOut,
        allowed_agents={"supervisor", "reviewer"},
    )
    async def post_review_comment(args: Any) -> dict[str, Any]:
        reg.calls.append(("post_review_comment", args.model_dump()))  # type: ignore[attr-defined]
        return {"dry_run": True}

    @reg.tool(
        name="push_fix_commit",
        description="push",
        input_schema=AnyIn,
        output_schema=AnyOut,
        allowed_agents={"fix_writer"},
    )
    async def push_fix_commit(args: Any) -> dict[str, Any]:
        reg.calls.append(("push_fix_commit", args.model_dump()))  # type: ignore[attr-defined]
        return {"dry_run": True}

    return reg


def plan_json(steps: list[dict[str, Any]], confidence: float = 0.9) -> str:
    return json.dumps({"steps": steps, "confidence": confidence, "rationale": "r"})


def step(id_: str, specialist: str, deps: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": id_,
        "description": f"do {id_}",
        "specialist": specialist,
        "depends_on": deps or [],
    }


TOOL_FOR = {
    "code_analysis": "run_ruff",
    "security": "run_bandit",
    "test_execution": "run_tests",
    "fix_writer": "run_tests",
}
FINDING_FOR = {"code_analysis": ("a.py", 3), "security": ("b.py", 9)}


class Behavior:
    """Drives specialists/reviewer by subtask id. fail_first: specialist emits junk on its first
    attempt. always_fail: junk on every attempt. reject_first: reviewer rejects the first
    reviewed run. fixes: attached to the output of subtask s1."""

    def __init__(
        self,
        plan: str,
        fail_first: set[str] | None = None,
        reject_first: set[str] | None = None,
        bad_citation: set[str] | None = None,
        always_fail: set[str] | None = None,
        fixes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.plan = plan
        self.fail_first = fail_first or set()
        self.reject_first = reject_first or set()
        self.bad_citation = bad_citation or set()
        self.always_fail = always_fail or set()
        self.fixes = fixes or []
        self.attempts: dict[str, int] = {}
        self.reviews: dict[str, int] = {}

    def __call__(self, role: str, messages: list[Message]) -> str:
        if role == "planner":
            try:
                last = json.loads(messages[-1]["content"])
            except ValueError:  # the supervisor's "plan rejected" re-ask is plain text
                return self.plan
            return self.plan if "request" in last else json.dumps({"summary": "all good"})
        last = json.loads(messages[-1]["content"])
        if role == "specialist":
            return self._specialist(messages, last)
        return self._reviewer(last)

    def _specialist(self, messages: list[Message], last: dict[str, Any]) -> str:
        first = json.loads(messages[1]["content"])
        sub = first["subtask"]
        sid, spec = sub["id"], sub["specialist"]
        if "observation" not in last:
            self.attempts[sid] = self.attempts.get(sid, 0) + 1
            if sid in self.always_fail or (sid in self.fail_first and self.attempts[sid] == 1):
                return "this is not json"
            return json.dumps({"action": "tool", "tool": TOOL_FOR[spec], "args": {}})
        findings = []
        if spec in FINDING_FOR:
            f, line = FINDING_FOR[spec]
            if sid in self.bad_citation:
                line = 999
            findings = [
                {
                    "file": f,
                    "line": line,
                    "rule": "R",
                    "severity": "high",
                    "evidence": "tool said so",
                }
            ]
        output: dict[str, Any] = {"note": sid}
        if sid == "s1" and self.fixes:
            output["fixes"] = self.fixes
        return json.dumps(
            {"action": "final", "output": output, "findings": findings, "confidence": 0.8}
        )

    def _reviewer(self, last: dict[str, Any]) -> str:
        sid = last["subtask"]["id"]
        self.reviews[sid] = self.reviews.get(sid, 0) + 1
        if sid in self.reject_first and self.reviews[sid] == 1:
            return json.dumps(
                {"approved": False, "score": 0.3, "issues": ["thin"], "feedback": "add detail"}
            )
        return json.dumps({"approved": True, "score": 0.9})


def fake_llm(behavior: Behavior) -> FakeLLM:
    return FakeLLM(responder=behavior)


class HashEmbedding:
    """Deterministic offline bag-of-words embedding for tests (no model download)."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        import re
        import zlib

        out = []
        for text in input:
            v = [0.0] * self.dim
            for w in re.findall(r"\w+", text.lower()):
                v[zlib.crc32(w.encode()) % self.dim] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out

    @staticmethod
    def name() -> str:
        return "hash-embedding"

    def get_config(self) -> dict[str, int]:
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config: dict[str, int]) -> HashEmbedding:
        return HashEmbedding(config.get("dim", 512))

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine", "l2", "ip"]

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)


def make_store(clock=None, **cfg):  # type: ignore[no-untyped-def]
    import uuid

    import chromadb

    from conductor.config import load_memory_config
    from conductor.db.session import init_db, make_engine, make_session_factory
    from conductor.memory.longterm import LongTermStore

    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    config = load_memory_config().model_copy(update=cfg)
    kwargs = {"now": clock} if clock else {}
    return LongTermStore(
        chromadb.EphemeralClient(),
        make_session_factory(engine),
        embedding_function=HashEmbedding(),
        config=config,
        prefix=uuid.uuid4().hex[:8] + "_",
        **kwargs,
    )


def make_graph(b: Behavior, saver=None, barrier: int = 0, wm=None, registry=None, store=None):  # type: ignore[no-untyped-def]
    """Build a graph over FakeLLM + fake tools. Pass the same saver to two calls to simulate a
    worker restart (new objects, same durable checkpoints)."""
    from langgraph.checkpoint.memory import MemorySaver

    from conductor.agents.reviewer import Reviewer
    from conductor.agents.specialists import build_specialists
    from conductor.agents.supervisor import Supervisor
    from conductor.config import load_agents_config, load_policies
    from conductor.graph.builder import GraphDeps, build_graph
    from conductor.memory.retrieval import Retriever

    llm = fake_llm(b)
    reg = registry or make_registry(barrier)
    policy = load_policies()
    deps = GraphDeps(
        supervisor=Supervisor(llm),
        specialists=build_specialists(llm, reg, load_agents_config()),
        reviewer=Reviewer(llm, policy.escalation.min_reviewer_score),
        policy_config=policy,
        wm=wm,
        retriever=Retriever(store) if store else None,
        registry=reg,
        memory_store=store,
    )
    graph = build_graph(deps, saver or MemorySaver())
    graph.llm = llm  # type: ignore[attr-defined]
    graph.registry = reg  # type: ignore[attr-defined]
    return graph
