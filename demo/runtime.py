"""CONDUCTOR_FAKE_LLM=1: the complete API on one process with no Postgres, Redis, Chroma server,
Docker or model quota. Same graph, same routes, same tracing; only the edges are replaced."""

from __future__ import annotations

import asyncio
import re
import threading
import zlib
from collections.abc import Callable
from typing import Any

import chromadb
from fastapi import FastAPI

from conductor.api.app import create_app
from conductor.config import Price
from conductor.db.session import init_db, make_engine, make_session_factory
from conductor.llm.fake import FakeLLM
from conductor.memory.longterm import LongTermStore
from conductor.obs.exporter import DbSpanExporter
from conductor.obs.replay import ReplayService
from conductor.obs.tracing import init_tracing
from conductor.tools.call_log import DbToolCallStore
from conductor.worker.runner import Runtime, execute_task, resume_task
from demo.fake_script import REPO, SEED_MEMORIES, USER, DemoScript, demo_registry

# Notional prices so the cost dashboard has something to show; real models use config/prices.yaml.
DEMO_PRICES = {
    f"fake/{role}": Price(input=400.0, output=1200.0)
    for role in ("planner", "specialist", "reviewer", "cheap")
}


class HashEmbedding:
    """Offline bag-of-words embedding: related text lands close together, no model download."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        out = []
        for text in input:
            v = [0.0] * self.dim
            for w in re.findall(r"\w+", text.lower()):
                v[zlib.crc32(w.encode()) % self.dim] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out

    @staticmethod
    def name() -> str:
        return "hash-embedding"

    def get_config(self) -> dict[str, int]:
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config: dict[str, int]) -> HashEmbedding:
        return HashEmbedding(config.get("dim", 512))

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine", "l2", "ip"]

    def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self(input)


def _in_background(coro_fn: Callable[..., Any]) -> Callable[..., None]:
    """Stand-in for Celery .delay(): run the coroutine on its own thread and event loop."""

    def enqueue(*args: Any) -> None:
        threading.Thread(target=lambda: asyncio.run(coro_fn(*args)), daemon=True).start()

    return enqueue


def build_demo_app() -> FastAPI:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    sf = make_session_factory(engine)
    init_tracing(DbSpanExporter(sf), batch=False)
    registry = demo_registry()
    registry.store = DbToolCallStore(sf)
    store = LongTermStore(chromadb.EphemeralClient(), sf, embedding_function=HashEmbedding())
    for collection, type_, text in SEED_MEMORIES:
        store.add(collection, text, user_id=USER, repo=REPO, type=type_)
    llm = FakeLLM(responder=DemoScript())
    rt = Runtime(
        session_factory=sf,
        llm=llm,
        registry=registry,
        memory_store=store,
        prices=DEMO_PRICES,
    )

    async def run(task_id: str) -> None:
        try:
            await execute_task(task_id, rt)
        except Exception:  # noqa: S110 - already recorded on the task row as status=failed
            pass

    async def resume(task_id: str, approval_id: str) -> None:
        try:
            await resume_task(task_id, approval_id, rt)
        except Exception:  # noqa: S110
            pass

    start, restart = _in_background(run), _in_background(resume)
    replay_llm = FakeLLM(responder=DemoScript())
    return create_app(
        sf,
        start,
        memory_store=store,
        enqueue_resume=restart,
        llm=llm,
        replay_service=ReplayService(sf, replay_llm, demo_registry(), DEMO_PRICES),
    )
