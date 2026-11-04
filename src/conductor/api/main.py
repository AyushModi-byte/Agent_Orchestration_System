"""Production app: uvicorn conductor.api.main:app. Enqueues onto Celery."""

from __future__ import annotations

from conductor.api.app import create_app
from conductor.db.session import init_db, make_engine, make_session_factory
from conductor.memory.longterm import LongTermStore, make_chroma_client
from conductor.worker.celery_app import run_task

_engine = make_engine()
init_db(_engine)
_sf = make_session_factory(_engine)
app = create_app(
    _sf,
    lambda tid: run_task.delay(tid),
    memory_store=LongTermStore(make_chroma_client(), _sf),
)
