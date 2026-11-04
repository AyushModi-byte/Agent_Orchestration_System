"""Tracing: a small tracer interface with an OpenTelemetry implementation behind it.

Callers use `get_tracer().span(...)` / `agent_span(...)`; they never import OpenTelemetry, so tests
and scripts run with the no-op default. `bind()` carries task_id / agent / subtask_id through
contextvars so every span, LLM-call row and tool-call row can be tied to the task that caused it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

_CTX: ContextVar[dict[str, Any] | None] = ContextVar("conductor_trace_ctx", default=None)


def _ctx() -> dict[str, Any]:
    return _CTX.get() or {}


class Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...


class Tracer(Protocol):
    def span(self, name: str, **attributes: Any) -> Any:
        """Context manager yielding a Span."""
        ...


@contextmanager
def bind(**values: Any) -> Iterator[None]:
    """Attach task_id / agent / subtask_id to everything done inside the block, including tasks
    spawned from it (LangGraph copies the context into parallel branches)."""
    token = _CTX.set({**_ctx(), **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        _CTX.reset(token)


def current(key: str, default: Any = None) -> Any:
    return _ctx().get(key, default)


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        return None


class NoopTracer:
    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        yield _NoopSpan()


class RecordedSpan:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = attributes

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class RecordingTracer:
    """In-memory tracer for tests that only need to see span names and attributes."""

    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        s = RecordedSpan(name, dict(attributes))
        self.spans.append(s)
        yield s


def _clean(value: Any) -> Any:
    """OpenTelemetry attributes must be primitives or homogeneous lists of them."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    return json.dumps(value, default=str)


class _OtelSpan:
    def __init__(self, span: Any) -> None:
        self._span = span
        self.status_set = False

    def set_attribute(self, key: str, value: Any) -> None:
        if value is None:
            return
        if key == "status":
            self.status_set = True
        self._span.set_attribute(key, _clean(value))


class OtelTracer:
    """Tracer over an OpenTelemetry TracerProvider (not the global one, so tests can use many)."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self._tracer = provider.get_tracer("conductor")

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[_OtelSpan]:
        merged = {
            **{k: v for k, v in _ctx().items() if k in ("task_id", "agent", "subtask_id")},
            **{k: v for k, v in attributes.items() if v is not None},
        }
        with self._tracer.start_as_current_span(
            name,
            attributes={k: _clean(v) for k, v in merged.items()},
            record_exception=False,
            set_status_on_exception=False,
        ) as raw:
            wrapped = _OtelSpan(raw)
            try:
                yield wrapped
            except BaseException as exc:
                raw.set_attribute("status", "error")
                raw.set_attribute("error", f"{type(exc).__name__}: {exc}"[:500])
                raise
            else:
                if not wrapped.status_set:
                    raw.set_attribute("status", "ok")

    def flush(self) -> None:
        self._provider.force_flush()

    def shutdown(self) -> None:
        self._provider.shutdown()


@contextmanager
def agent_span(
    name: str, agent: str, subtask_id: str | None = None, **attributes: Any
) -> Iterator[Span]:
    """A span for one agent's unit of work; nested LLM and tool spans inherit the agent."""
    with (
        bind(agent=agent, subtask_id=subtask_id),
        get_tracer().span(name, agent=agent, subtask_id=subtask_id, **attributes) as s,
    ):
        yield s


_tracer: Tracer = NoopTracer()


def get_tracer() -> Tracer:
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer


def init_tracing(exporter: Any, batch: bool = True) -> OtelTracer:
    """Install an OpenTelemetry tracer that writes finished spans through `exporter`.

    batch=False exports each span as it ends, which tests use for deterministic reads.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    )
    tracer = OtelTracer(provider)
    set_tracer(tracer)
    return tracer
