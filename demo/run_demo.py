"""`make demo`. --fake (default) is scripted and spends nothing; --live uses the real models
through the gateway and a real GitHub repo made by demo/seed_target_repo.py.

--live reads answers from the committed cassette cache (demo/cassettes) and only calls a provider
when LIVE_LLM=1 and a prompt is missing from it. Pushes stay dry runs unless DRY_RUN=0.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from conductor.hitl.queue import Decision
from conductor.worker.runner import Runtime
from demo.scenario import ScenarioResult, run_scenario

CASSETTE = Path(__file__).parent / "cassettes" / "demo.llmcache"


def build_live_runtime() -> Runtime:
    """Real gateway, real GitHub tools, persistent stores. Imported lazily: fake mode must not
    need provider SDKs, Chroma models or Docker."""
    from conductor.db.session import init_db, make_engine, make_session_factory
    from conductor.llm.cache import DiskCache
    from conductor.llm.gateway import LLMGateway
    from conductor.memory.longterm import LongTermStore, make_chroma_client
    from conductor.obs.exporter import DbSpanExporter
    from conductor.obs.tracing import init_tracing
    from conductor.tools import deps, github, linters  # noqa: F401  (import registers tools)
    from conductor.tools.call_log import DbToolCallStore
    from conductor.tools.registry import default_registry

    CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine("sqlite:///.cache/demo.db")
    init_db(engine)
    sf = make_session_factory(engine)
    init_tracing(DbSpanExporter(sf), batch=False)
    default_registry.store = DbToolCallStore(sf)
    os.environ.setdefault("CHROMA_PATH", ".cache/chroma-demo")
    return Runtime(
        session_factory=sf,
        llm=LLMGateway(cache=DiskCache(CASSETTE)),
        registry=default_registry,
        memory_store=LongTermStore(make_chroma_client(), sf),
    )


def decider(auto: bool) -> Any:
    def decide(ctx: dict[str, Any]) -> Decision:
        prop = ctx.get("proposed_action") or {}
        print(f"\n  Proposed: {prop.get('summary')}")
        print(f"  Reasoning: {prop.get('reasoning')}")
        if auto or not sys.stdin.isatty():
            print("  -> auto-approving (--yes or no terminal)")
            return Decision(type="approve")
        answer = input("  Approve? [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            return Decision(type="reject", reason=input("  Reason: ").strip() or "not now")
        return Decision(type="approve")

    return decide


def summarize(r: ScenarioResult) -> None:
    ok = (
        r.pr2.status == "completed"
        and r.pr2.memories_recalled > 0
        and r.pr2.reviewer_rejections > 0
        and r.pr2.decision == "approve"
    )
    print("\nDemo " + ("complete." if ok else "finished, but not every beat happened."))
    print(f"  PR 1: {r.pr1.status}, {r.pr1.lessons_saved} lessons saved")
    print(
        f"  PR 2: {r.pr2.status}, {r.pr2.memories_recalled} memories recalled, "
        f"{r.pr2.reviewer_rejections} fix rejected by the reviewer, human said {r.pr2.decision}"
    )
    print("  Nothing was merged or deployed: that is always a human's job.")


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Conductor showcase demo")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted, zero quota (default)")
    mode.add_argument("--live", action="store_true", help="real models via cassettes + GitHub")
    ap.add_argument("--yes", action="store_true", help="approve the human gate without asking")
    ap.add_argument("--repo", default=os.environ.get("DEMO_REPO", "acme/shop"))
    args = ap.parse_args(argv)

    if args.live:
        if not os.environ.get("GITHUB_TOKEN") and os.environ.get("LIVE_LLM") == "1":
            print("--live needs GITHUB_TOKEN and DEMO_REPO (run demo/seed_target_repo.py first).")
            return 2
        rt, user = build_live_runtime(), "demo"
        repo = args.repo
    else:
        from demo.fake_script import USER
        from demo.runtime import build_demo_runtime

        rt, user, repo = build_demo_runtime(seed=False), USER, "acme/shop"
        print("Fake mode: scripted models and canned tools, no network, no quota.\n")

    result = await run_scenario(
        rt, repo=repo, user=user, decide=decider(args.yes), say=lambda m: print(m)
    )
    summarize(result)
    return 0 if result.pr2.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
