"""Stub tracer interface. The OpenTelemetry-backed implementation lands in the obs phase;
callers depend only on this protocol so swapping it in changes no call sites."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol


class Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...


class Tracer(Protocol):
    def span(self, name: str, **attributes: Any) -> Any:
        """Context manager yielding a Span."""
        ...


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
    """In-memory tracer for tests."""

    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        s = RecordedSpan(name, dict(attributes))
        self.spans.append(s)
        yield s


_tracer: Tracer = NoopTracer()


def get_tracer() -> Tracer:
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer
