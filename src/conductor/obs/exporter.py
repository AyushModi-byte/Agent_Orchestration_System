"""OpenTelemetry exporter that batch-writes finished spans to the `spans` table."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from sqlalchemy.orm import Session, sessionmaker

from conductor.db.models import SpanRow


def _ts(ns: int | None) -> datetime:
    return datetime.fromtimestamp((ns or 0) / 1e9, tz=UTC)


def to_row(span: ReadableSpan) -> SpanRow:
    attrs: dict[str, Any] = {
        k: (list(v) if isinstance(v, tuple) else v) for k, v in (span.attributes or {}).items()
    }
    ctx = span.get_span_context()
    assert ctx is not None
    start, end = span.start_time or 0, span.end_time or 0
    return SpanRow(
        trace_id=format(ctx.trace_id, "032x"),
        span_id=format(ctx.span_id, "016x"),
        parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
        task_id=str(attrs.get("task_id", "")),
        name=span.name,
        agent=attrs.get("agent"),
        role=attrs.get("role"),
        model=attrs.get("model"),
        status=str(attrs.get("status", "ok")),
        error=attrs.get("error"),
        start_time=_ts(start),
        end_time=_ts(end),
        duration_ms=(end - start) / 1e6,
        tokens_in=int(attrs.get("tokens_in", 0)),
        tokens_out=int(attrs.get("tokens_out", 0)),
        cost_usd=float(attrs.get("cost_usd", 0.0)),
        llm_call_id=attrs.get("llm_call_id"),
        attributes=attrs,
    )


class DbSpanExporter(SpanExporter):
    """One transaction per batch. A tracing failure must never break a task, so errors are
    reported to OpenTelemetry as FAILURE instead of raised."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self._sf() as s:
                s.add_all([to_row(sp) for sp in spans])
                s.commit()
        except Exception:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
