from __future__ import annotations

import pytest
from pydantic import BaseModel

from conductor.obs.tracing import RecordingTracer, set_tracer
from conductor.tools.registry import (
    PermissionDenied,
    RateLimitExceeded,
    ToolNotFound,
    ToolRegistry,
    ToolValidationError,
)


class In(BaseModel):
    x: int


class Out(BaseModel):
    y: int


def make(clock=lambda: 0.0, rate_limit=None, handler_result=None) -> ToolRegistry:
    reg = ToolRegistry(clock=clock)

    @reg.tool(
        name="double",
        description="d",
        input_schema=In,
        output_schema=Out,
        allowed_agents={"a"},
        rate_limit=rate_limit,
    )
    async def double(args: In) -> object:
        return handler_result if handler_result is not None else {"y": args.x * 2}

    return reg


async def test_success_logs_row_and_span() -> None:
    tracer = RecordingTracer()
    set_tracer(tracer)
    reg = make()
    out = await reg.invoke("a", "double", {"x": 2})
    assert out == Out(y=4)
    (row,) = reg.store.all()
    assert row.success and row.tool == "double" and row.agent == "a"
    assert "4" in (row.outputs or "") and "2" in row.inputs and row.latency_ms >= 0
    assert tracer.spans[0].name == "tool.call"
    assert tracer.spans[0].attributes["success"] is True


async def test_permission_denied_is_logged() -> None:
    reg = make()
    with pytest.raises(PermissionDenied):
        await reg.invoke("intruder", "double", {"x": 1})
    (row,) = reg.store.all()
    assert not row.success and "PermissionDenied" in (row.error or "")


async def test_unknown_tool() -> None:
    with pytest.raises(ToolNotFound):
        await make().invoke("a", "nope", {})


async def test_rate_limit_hit_then_window_slides() -> None:
    now = 0.0
    reg = make(clock=lambda: now, rate_limit=2)
    await reg.invoke("a", "double", {"x": 1})
    await reg.invoke("a", "double", {"x": 1})
    with pytest.raises(RateLimitExceeded):
        await reg.invoke("a", "double", {"x": 1})
    now = 61.0
    await reg.invoke("a", "double", {"x": 1})


async def test_input_schema_failure() -> None:
    reg = make()
    with pytest.raises(ToolValidationError):
        await reg.invoke("a", "double", {"x": "not-an-int"})
    assert not reg.store.all()[0].success


async def test_output_schema_failure() -> None:
    reg = make(handler_result={"wrong": 1})
    with pytest.raises(ToolValidationError):
        await reg.invoke("a", "double", {"x": 1})
    assert not reg.store.all()[0].success


def test_no_merge_or_deploy_tool_exists() -> None:
    from conductor.tools import deps, github, linters  # noqa: F401
    from conductor.tools.registry import default_registry

    names = {t.name for t in default_registry.tools()}
    assert not {n for n in names if "merge" in n or "deploy" in n}
