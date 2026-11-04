"""Production app: uvicorn conductor.api.main:app. Enqueues onto Celery."""

from __future__ import annotations

from conductor.api.app import create_app
from conductor.db.session import init_db, make_engine, make_session_factory
from conductor.llm.gateway import LLMGateway
from conductor.memory.longterm import LongTermStore, make_chroma_client
from conductor.obs.exporter import DbSpanExporter
from conductor.obs.replay import ReplayService
from conductor.obs.tracing import init_tracing
from conductor.tools.call_log import DbToolCallStore
from conductor.tools.registry import default_registry
from conductor.worker.celery_app import resume_task, run_task

_engine = make_engine()
init_db(_engine)
_sf = make_session_factory(_engine)
init_tracing(DbSpanExporter(_sf))
default_registry.store = DbToolCallStore(_sf)
_llm = LLMGateway()
app = create_app(
    _sf,
    lambda tid: run_task.delay(tid),
    memory_store=LongTermStore(make_chroma_client(), _sf),
    enqueue_resume=lambda tid, aid: resume_task.delay(tid, aid),
    llm=_llm,
    replay_service=ReplayService(_sf, _llm, default_registry),
)
