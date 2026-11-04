"""Bounded tool-use loop shared by all specialists.

Each turn the model either calls one permitted tool or finishes. The loop is capped by
config/agents.yaml so a confused model cannot burn quota, and every tool result is recorded as a
ToolTrace so the reviewer can check claims against what the tools really returned.
"""

from __future__ import annotations

import json
from typing import Any

from conductor.agents.prompts_loader import load_prompt
from conductor.agents.schemas import SpecialistResult, StepDecision, Subtask, ToolTrace
from conductor.llm.types import LLM, SchemaValidationError
from conductor.tools.registry import ToolRegistry

OBSERVATION_LIMIT = 6000


class SpecialistError(Exception):
    """The specialist could not produce a result; the graph retries with a different approach."""


def _locations(obj: Any) -> list[tuple[str, int]]:
    """Collect (file, line) pairs from arbitrary tool output."""
    found: list[tuple[str, int]] = []
    if isinstance(obj, dict):
        file = obj.get("file") or obj.get("filename")
        line = obj.get("line") if obj.get("line") is not None else obj.get("line_number")
        if isinstance(file, str) and isinstance(line, int) and not isinstance(line, bool):
            found.append((file, line))
        for v in obj.values():
            found.extend(_locations(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_locations(v))
    return found


def make_trace(
    tool: str, args: dict[str, Any], output: dict[str, Any] | None, err: str
) -> ToolTrace:
    if output is None:
        return ToolTrace(tool=tool, args=args, ok=False, summary=err[:300])
    metrics = {k: v for k, v in output.items() if isinstance(v, int) and not isinstance(v, bool)}
    if isinstance(output.get("content"), str):
        metrics["lines"] = len(output["content"].splitlines())
    return ToolTrace(
        tool=tool,
        args=args,
        ok=True,
        summary=json.dumps(output, default=str)[:300],
        locations=_locations(output),
        metrics=metrics,
    )


class Specialist:
    def __init__(
        self,
        name: str,
        llm: LLM,
        registry: ToolRegistry,
        tools: list[str],
        max_iterations: int,
    ) -> None:
        self.name = name
        self._llm = llm
        self._registry = registry
        self.tools = tools
        self._max = max_iterations

    def _tool_defs(self) -> list[dict[str, Any]]:
        defs = []
        for n in self.tools:
            try:
                t = self._registry.get(n)
            except Exception:
                continue
            defs.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema.model_json_schema(),
                }
            )
        return defs

    async def run(
        self,
        subtask: Subtask,
        context: dict[str, Any],
        upstream: dict[str, Any],
        hint: str | None = None,
        feedback: str | None = None,
    ) -> SpecialistResult:
        payload = {
            "subtask": subtask.model_dump(),
            "context": context,
            "upstream": upstream,
            "tools": self._tool_defs(),
            "hint": hint,
            "feedback": feedback,
        }
        msgs = [
            {"role": "system", "content": load_prompt(self.name)},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]
        traces: list[ToolTrace] = []
        for _ in range(self._max):
            try:
                res = await self._llm.complete("specialist", msgs, schema=StepDecision)
            except SchemaValidationError as exc:
                raise SpecialistError(f"invalid model output: {exc}") from exc
            step = res.parsed
            assert isinstance(step, StepDecision)
            if step.action == "final":
                return SpecialistResult(
                    subtask_id=subtask.id,
                    output=step.output,
                    findings=step.findings,
                    tool_summary=traces,
                    confidence=step.confidence,
                )
            observation = await self._call_tool(step, traces)
            msgs = [
                *msgs,
                {"role": "assistant", "content": res.text},
                {"role": "user", "content": json.dumps({"observation": observation})},
            ]
        raise SpecialistError(f"no final answer within {self._max} iterations")

    async def _call_tool(self, step: StepDecision, traces: list[ToolTrace]) -> dict[str, Any]:
        name = step.tool or ""
        if name not in self.tools:
            err = f"tool {name!r} is not permitted for {self.name}"
            traces.append(make_trace(name, step.args, None, err))
            return {"error": err}
        try:
            out = (await self._registry.invoke(self.name, name, step.args)).model_dump()
        except Exception as exc:  # fed back so the model can adapt instead of aborting
            err = f"{type(exc).__name__}: {exc}"
            traces.append(make_trace(name, step.args, None, err))
            return {"error": err}
        traces.append(make_trace(name, step.args, out, ""))
        return {"result": json.dumps(out, default=str)[:OBSERVATION_LIMIT]}
