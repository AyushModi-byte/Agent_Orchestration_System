"""Tool registry: the only door from agents to the outside world.

Every call is permission-checked, rate-limited, schema-validated both ways, logged and traced,
so a misbehaving agent cannot reach a tool it was not granted.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor.obs.tracing import get_tracer
from conductor.tools.call_log import SqliteToolCallStore, ToolCallRecord, ToolCallStore


class ToolError(Exception):
    """Base for registry errors."""


class ToolNotFound(ToolError): ...


class PermissionDenied(ToolError): ...


class RateLimitExceeded(ToolError): ...


class ToolValidationError(ToolError): ...


Handler = Callable[[Any], Awaitable[Any]]


class Tool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    allowed_agents: frozenset[str]
    rate_limit: int | None = Field(default=None, description="max calls per minute")
    handler: Handler = Field(exclude=True)


class ToolRegistry:
    def __init__(
        self,
        store: ToolCallStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._calls: dict[str, deque[float]] = {}
        self.store: ToolCallStore = store or SqliteToolCallStore()
        self._clock = clock

    def tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: type[BaseModel],
        output_schema: type[BaseModel],
        allowed_agents: set[str] | frozenset[str],
        rate_limit: int | None = None,
    ) -> Callable[[Handler], Handler]:
        """Decorator registering an async handler taking the validated input model."""

        def deco(fn: Handler) -> Handler:
            if name in self._tools:
                raise ValueError(f"tool {name!r} already registered")
            self._tools[name] = Tool(
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                allowed_agents=frozenset(allowed_agents),
                rate_limit=rate_limit,
                handler=fn,
            )
            return fn

        return deco

    def derive(
        self,
        wrap: Callable[[Tool], Handler],
        store: ToolCallStore | None = None,
    ) -> ToolRegistry:
        """A registry with the same tools, schemas and permissions but different handlers.
        Replay uses this to serve recorded results through the normal invoke() path."""
        other = ToolRegistry(store=store, clock=self._clock)
        for name, t in self._tools.items():
            other._tools[name] = t.model_copy(update={"handler": wrap(t), "rate_limit": None})
        return other

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(name) from None

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def _check_rate(self, tool: Tool) -> None:
        if tool.rate_limit is None:
            return
        now = self._clock()
        window = self._calls.setdefault(tool.name, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= tool.rate_limit:
            raise RateLimitExceeded(f"{tool.name}: {tool.rate_limit} calls/minute")
        window.append(now)

    async def invoke(self, agent: str, tool: str, args: dict[str, Any]) -> BaseModel:
        start = time.perf_counter()
        outputs: str | None = None
        error: str | None = None
        with get_tracer().span("tool.call", agent=agent, tool=tool) as span:
            try:
                t = self.get(tool)
                if agent not in t.allowed_agents:
                    raise PermissionDenied(f"agent {agent!r} may not use tool {tool!r}")
                self._check_rate(t)
                try:
                    parsed = t.input_schema.model_validate(args)
                except ValidationError as exc:
                    raise ToolValidationError(f"{tool} input: {exc}") from exc
                result = await t.handler(parsed)
                if isinstance(result, BaseModel):
                    result = result.model_dump()
                try:
                    out = t.output_schema.model_validate(result)
                except ValidationError as exc:
                    raise ToolValidationError(f"{tool} output: {exc}") from exc
                outputs = out.model_dump_json()
                return out
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                latency = (time.perf_counter() - start) * 1000
                span.set_attribute("tool", tool)
                span.set_attribute("success", error is None)
                span.set_attribute("status", "ok" if error is None else "error")
                span.set_attribute("error", error)
                span.set_attribute("latency_ms", latency)
                self.store.record(
                    ToolCallRecord(
                        agent=agent,
                        tool=tool,
                        inputs=json.dumps(args, sort_keys=True, default=str),
                        outputs=outputs,
                        latency_ms=latency,
                        success=error is None,
                        error=error,
                    )
                )


default_registry = ToolRegistry()
tool = default_registry.tool
