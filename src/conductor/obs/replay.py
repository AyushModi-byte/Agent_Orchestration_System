"""Replay: re-run a past task, optionally overriding one decision, and diff it against the original.

How it works. A task's decisions are its recorded LLM calls and tool calls (llm_calls / tool_calls
rows). A replay re-executes the whole graph, but every model call and tool call first looks for
the same request in the original recording and, if found, is served from it: free and identical.
An override replaces one recorded answer (a different plan, a changed tool result). Everything
downstream whose inputs changed no longer matches a recording, so it runs for real through the
gateway (and its disk cache); everything unaffected stays free. Nothing is ever written to the
outside world: write tools are forced to a dry run.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from conductor.config import Price, load_policies, load_prices
from conductor.db.models import Approval, LLMCall, Replay, Task, ToolCall
from conductor.graph.builder import build_graph
from conductor.llm.gateway import parse_model
from conductor.llm.recording import RecordingLLM
from conductor.llm.types import LLM, LLMResult, Message, SchemaValidationError, Usage
from conductor.obs.cost import task_cost
from conductor.obs.tracing import bind, get_tracer
from conductor.tools.call_log import DbToolCallStore
from conductor.tools.registry import Tool, ToolRegistry
from conductor.worker.runner import make_deps, make_llm_sink

# These fail inside invoke() before any handler runs, so there is nothing to record or replay.
_REGISTRY_ERRORS = ("PermissionDenied", "RateLimitExceeded", "ToolValidationError", "ToolNotFound")


def request_key(role: str, messages: list[Message], schema_name: str | None) -> str:
    blob = json.dumps([role, messages, schema_name], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class Step(BaseModel):
    """One recorded decision: a model call or a tool call."""

    index: int
    kind: Literal["llm", "tool"]
    agent: str
    name: str  # role for llm, tool name for tool
    subtask: str
    ordinal: int  # nth step with this (kind, agent, name, subtask); stable across replays
    key: str
    input_preview: str
    raw_input: str = ""
    output: Any
    error: str | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.agent}:{self.name}:{self.subtask}:{self.ordinal}"


def load_steps(sf: sessionmaker[Session], task_id: str) -> list[Step]:
    with sf() as s:
        llm = s.query(LLMCall).filter(LLMCall.task_id == task_id).all()
        tools = s.query(ToolCall).filter(ToolCall.task_id == task_id).all()
    rows: list[tuple[Any, ...]] = [
        (c.created_at, 0, c.id, c) for c in llm if c.agent != "memory"
    ] + [(t.created_at, 1, t.id, t) for t in tools]
    rows.sort(key=lambda r: r[:3])
    counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    steps: list[Step] = []
    for _, kind, _, row in rows:
        if kind == 0:
            agent, name, subtask = row.agent or "unknown", row.role, row.subtask_id or ""
            msgs = row.request or []
            key = request_key(row.role, msgs, row.schema_name)
            preview = (msgs[-1]["content"] if msgs else "")[:300]
            out: Any = row.response
            extra: dict[str, Any] = {
                "model": row.model,
                "tokens_in": row.tokens_in,
                "tokens_out": row.tokens_out,
                "cost_usd": row.cost_usd,
            }
            k = "llm"
        else:
            agent, name, subtask = row.agent, row.tool, row.subtask_id or ""
            key = f"{row.tool}:{row.inputs}"
            preview = row.inputs[:300]
            out = json.loads(row.outputs) if row.outputs else None
            extra = {"error": row.error, "raw_input": row.inputs}
            k = "tool"
        ordinal = counts[(k, agent, name, subtask)]
        counts[(k, agent, name, subtask)] += 1
        steps.append(
            Step(
                index=len(steps),
                kind=k,
                agent=agent,
                name=name,
                subtask=subtask,
                ordinal=ordinal,
                key=key,
                input_preview=preview,
                output=out,
                **extra,
            )
        )
    return steps


class Override(BaseModel):
    step: int
    value: Any  # llm step: replacement response (str or JSON); tool step: replacement output dict


class ReplayLLM:
    """Serves recorded (or overridden) answers; anything unrecorded goes to the real LLM."""

    def __init__(self, steps: list[Step], overrides: dict[int, Any], inner: LLM) -> None:
        self._inner = inner
        self._rec: dict[str, list[Step]] = defaultdict(list)
        for st in steps:
            if st.kind == "llm":
                self._rec[st.key].append(st)
        self._over: dict[tuple[str, int], str] = {}
        for idx, val in overrides.items():
            st = steps[idx]
            if st.kind != "llm":
                continue
            occ = next(i for i, x in enumerate(self._rec[st.key]) if x.index == idx)
            self._over[(st.key, occ)] = val if isinstance(val, str) else json.dumps(val)
        self._seen: dict[str, int] = defaultdict(int)
        self.live_calls = 0
        self.served = 0

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult:
        key = request_key(role, messages, schema.__name__ if schema else None)
        n = self._seen[key]
        self._seen[key] += 1
        recorded = self._rec.get(key)
        if (key, n) in self._over:
            text, model, tin, tout = self._over[(key, n)], "override", 0, 0
        elif recorded:
            st = recorded[min(n, len(recorded) - 1)]
            text, model, tin, tout = str(st.output), st.model, st.tokens_in, st.tokens_out
        else:
            self.live_calls += 1
            return await self._inner.complete(role, messages, schema)
        self.served += 1
        try:
            parsed = parse_model(text, schema) if schema else None
        except (ValueError, ValidationError) as exc:
            raise SchemaValidationError(f"replayed answer is not valid: {exc}") from exc
        return LLMResult(
            text=text,
            parsed=parsed,
            usage=Usage(model=model, tokens_in=tin, tokens_out=tout, cache_hit=True),
        )


def _recorded_error(error: str) -> Exception:
    """Rebuild an exception whose class name and message match the recorded one, so the specialist
    feeds the model the identical observation text."""
    name, _, msg = error.partition(": ")
    cls: type[Exception] = type(name or "RecordedError", (Exception,), {})
    return cls(msg)


def replay_registry(
    real: ToolRegistry,
    steps: list[Step],
    overrides: dict[int, Any],
    store: DbToolCallStore,
    write_tools: set[str],
) -> ToolRegistry:
    def nkey(tool: str, inputs: str) -> str | None:
        try:
            parsed = real.get(tool).input_schema.model_validate(json.loads(inputs))
        except Exception:
            return None
        return tool + ":" + json.dumps(parsed.model_dump(), sort_keys=True, default=str)

    rec: dict[str, list[Step]] = defaultdict(list)
    keys: dict[int, str] = {}
    for st in steps:
        if st.kind != "tool" or (st.error or "").startswith(_REGISTRY_ERRORS):
            continue
        if (k := nkey(st.name, st.raw_input)) is not None:
            rec[k].append(st)
            keys[st.index] = k
    over: dict[tuple[str, int], Any] = {}
    for idx, val in overrides.items():
        if idx in keys:
            occ = next(i for i, x in enumerate(rec[keys[idx]]) if x.index == idx)
            over[(keys[idx], occ)] = val
    seen: dict[str, int] = defaultdict(int)

    def wrap(t: Tool) -> Any:
        async def handler(args: BaseModel) -> Any:
            k = t.name + ":" + json.dumps(args.model_dump(), sort_keys=True, default=str)
            n = seen[k]
            seen[k] += 1
            if (k, n) in over:
                return over[(k, n)]
            if recorded := rec.get(k):
                st = recorded[min(n, len(recorded) - 1)]
                if st.error:
                    raise _recorded_error(st.error)
                return st.output
            if t.name in write_tools:  # a diverged run must never touch the outside world
                return {"dry_run": True}
            return await t.handler(args)

        return handler

    return real.derive(wrap, store)


class StepChange(BaseModel):
    identity: str
    kind: str
    agent: str
    name: str
    before: Any = None
    after: Any = None


class ReplayDiff(BaseModel):
    replay_id: str
    source_task_id: str
    overridden_steps: list[int]
    diverged: bool
    changed_steps: list[StepChange]
    added_steps: list[StepChange]
    removed_steps: list[StepChange]
    unchanged_steps: int
    live_calls: int  # calls that could not be served from the recording
    cost: dict[str, float]
    status: dict[str, Any]
    report_changed: bool


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


class ReplayService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        llm: LLM,
        registry: ToolRegistry,
        prices: dict[str, Price] | None = None,
    ) -> None:
        self._sf = session_factory
        self._llm = llm
        self._registry = registry
        self._prices = prices if prices is not None else load_prices()

    def steps(self, task_id: str) -> list[Step]:
        return load_steps(self._sf, task_id)

    async def run(self, source_task_id: str, overrides: list[Override] | None = None) -> str:
        overrides = overrides or []
        with self._sf() as s:
            task = s.get(Task, source_task_id)
            if task is None:
                raise LookupError(source_task_id)
            request = dict(task.request)
            decisions = [
                dict(a.decision)
                for a in s.query(Approval)
                .filter(Approval.task_id == source_task_id)
                .order_by(Approval.created_at)
                if a.decision
            ]
            first_plan = (
                s.query(LLMCall)
                .filter(LLMCall.task_id == source_task_id, LLMCall.agent == "supervisor")
                .order_by(LLMCall.id)
                .first()
            )
        steps = load_steps(self._sf, source_task_id)
        ov = {o.step: o.value for o in overrides}
        for i in ov:
            if not 0 <= i < len(steps):
                raise ValueError(f"step {i} does not exist (task has {len(steps)} steps)")

        # The original planner prompt carried the memories retrieved at the time; replaying
        # without them would change the prompt and defeat the recording.
        memories: list[str] = []
        if first_plan and first_plan.request:
            try:
                memories = json.loads(first_plan.request[-1]["content"]).get("memories", [])
            except (ValueError, KeyError, AttributeError):
                memories = []

        rid = "replay-" + uuid.uuid4().hex[:12]
        with self._sf() as s:
            s.add(
                Replay(
                    id=rid,
                    source_task_id=source_task_id,
                    overrides=[o.model_dump() for o in overrides],
                )
            )
            s.commit()

        policy = load_policies()
        write_tools = {*policy.sensitive.tools, *policy.actions.keys()}
        replay_llm = ReplayLLM(steps, ov, self._llm)
        llm = RecordingLLM(replay_llm, make_llm_sink(self._sf, rid), self._prices)
        registry = replay_registry(
            self._registry, steps, ov, DbToolCallStore(self._sf), write_tools
        )
        graph = build_graph(make_deps(llm, registry), MemorySaver())
        cfg = {"configurable": {"thread_id": rid}}

        status, error = "completed", None
        final: dict[str, Any] = {}
        try:
            with (
                bind(task_id=rid),
                get_tracer().span(
                    "replay.run", source_task_id=source_task_id, overrides=len(overrides)
                ),
            ):
                graph_input: Any = {"task_id": rid, "request": request, "memories": memories}
                while True:
                    interrupted = False
                    async for chunk in graph.astream(graph_input, cfg, stream_mode="values"):
                        interrupted = "__interrupt__" in chunk
                        final = {k: v for k, v in chunk.items() if k != "__interrupt__"}
                    if not interrupted:
                        status = final.get("status", "completed")
                        break
                    if not decisions:  # the replay reached a gate the original never had
                        status = "awaiting_human"
                        break
                    graph_input = Command(resume=decisions.pop(0))
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        cost = task_cost(self._sf, rid).cost_usd
        with self._sf() as s:
            row = s.get(Replay, rid)
            assert row is not None
            row.status = status
            row.error = error
            row.result = {
                "status": final.get("status"),
                "report": final.get("report"),
                "plan": final.get("plan"),
            }
            row.live_calls = replay_llm.live_calls
            row.cost_usd = cost
            s.commit()
        return rid

    def get(self, replay_id: str) -> Replay | None:
        with self._sf() as s:
            return s.get(Replay, replay_id)

    def diff(self, replay_id: str) -> ReplayDiff:
        with self._sf() as s:
            rep = s.get(Replay, replay_id)
            if rep is None:
                raise LookupError(replay_id)
            task = s.get(Task, rep.source_task_id)
            assert task is not None
            src_status, src_report = task.status, task.report
        before = {st.identity: st for st in load_steps(self._sf, rep.source_task_id)}
        after = {st.identity: st for st in load_steps(self._sf, replay_id)}

        def change(a: Step | None, b: Step | None) -> StepChange:
            ref = a or b
            assert ref is not None
            return StepChange(
                identity=ref.identity,
                kind=ref.kind,
                agent=ref.agent,
                name=ref.name,
                before=a.output if a else None,
                after=b.output if b else None,
            )

        changed = [
            change(before[i], after[i])
            for i in before
            if i in after and not _same(before[i].output, after[i].output)
        ]
        added = [change(None, after[i]) for i in after if i not in before]
        removed = [change(before[i], None) for i in before if i not in after]
        original_cost = sum(st.cost_usd for st in before.values() if st.kind == "llm")
        replay_cost = rep.cost_usd
        res = rep.result or {}
        return ReplayDiff(
            replay_id=replay_id,
            source_task_id=rep.source_task_id,
            overridden_steps=[o["step"] for o in rep.overrides],
            diverged=bool(changed or added or removed),
            changed_steps=changed,
            added_steps=added,
            removed_steps=removed,
            unchanged_steps=sum(
                1 for i in before if i in after and _same(before[i].output, after[i].output)
            ),
            live_calls=rep.live_calls,
            cost={
                "original": original_cost,
                "replay": replay_cost,
                "delta": replay_cost - original_cost,
            },
            status={"before": src_status, "after": res.get("status")},
            report_changed=not _same(src_report, res.get("report")),
        )
