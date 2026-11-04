"""Production app: uvicorn conductor.api.main:app. Enqueues onto Celery.

CONDUCTOR_FAKE_LLM=1 swaps every external edge for in-process fakes (see demo/runtime.py) so the
UI can be built and shown without Postgres, Redis, Docker or quota. It never reaches a provider.
"""

from __future__ import annotations

import os
from importlib import import_module

from fastapi import FastAPI

from conductor.api.app import create_app


def build_app() -> FastAPI:
    if os.environ.get("CONDUCTOR_FAKE_LLM") == "1":
        app: FastAPI = import_module("demo.runtime").build_demo_app()
        return app

    from conductor.db.session import init_db, make_engine, make_session_factory
    from conductor.llm.gateway import LLMGateway
    from conductor.memory.longterm import LongTermStore, make_chroma_client
    from conductor.obs.exporter import DbSpanExporter
    from conductor.obs.replay import ReplayService
    from conductor.obs.tracing import init_tracing
    from conductor.tools.call_log import DbToolCallStore
    from conductor.tools.registry import default_registry
    from conductor.worker.celery_app import resume_task, run_task

    engine = make_engine()
    init_db(engine)
    sf = make_session_factory(engine)
    init_tracing(DbSpanExporter(sf))
    default_registry.store = DbToolCallStore(sf)
    llm = LLMGateway()
    return create_app(
        sf,
        lambda tid: run_task.delay(tid),
        memory_store=LongTermStore(make_chroma_client(), sf),
        enqueue_resume=lambda tid, aid: resume_task.delay(tid, aid),
        llm=llm,
        replay_service=ReplayService(sf, llm, default_registry),
    )


app = build_app()
