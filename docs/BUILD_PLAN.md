# Conductor: Build Plan

## Overview
Conductor is a multi-agent platform that reviews GitHub pull requests: a supervisor plans the work, specialist agents analyse, scan, test and fix code with real tools, a reviewer gates every output, a human approves sensitive actions, and memory makes the next review smarter. Every decision is traced and replayable. (Name is a placeholder.)

Demo task: "Review PR #N in repo X: find bugs and security issues, propose fixes, run tests, prepare for merge."

## Scope decisions
- Everything in the original spec is built: agents, tool registry, short- and long-term memory, four approval levels, tracing, cost tracking, replay, React UI, Docker, tests, demo.
- Target repos are Python-only in v1. Adding JS needs a second linter/test toolchain in the sandbox image (build time, not runtime cost), so it is v2.
- LLMs are free tier only: Gemini and Groq behind one gateway with caching and rate limiting.
- Claude Code is the build tool, with a test strategy that spends almost no LLM quota.

## Deliberate deviations from the spec
- Celery runs whole task executions and sandbox jobs; LangGraph's parallel fan-out handles specialists.
- Pause/resume uses LangGraph interrupt() plus a Postgres checkpointer instead of hand-rolled state.
- Embeddings run locally (free, no API quota).
- Cost is tracked as notional USD from a price table, since the free tier bills nothing.
- The system can never merge or deploy. Merge is human-only by design ("Take over" level).

## Resume line
Built a multi-agent code-review orchestrator (LangGraph, FastAPI, React) with sandboxed tool use, semantic long-term memory, tiered human-in-the-loop approvals, OpenTelemetry tracing and deterministic replay.

## Free-tier and limit strategy
Real LLM calls are rare, deliberate and cached.

| Tier | LLM used | When it runs | Cost |
| --- | --- | --- | --- |
| T0 Unit | FakeLLM (scripted) | Every change, CI | Zero |
| T1 Integration | Disk cache of recorded real responses | Every phase gate, CI | Zero after first recording |
| T2 Live smoke | Real Gemini/Groq, one tiny task | At most once per phase, LIVE_LLM=1 pytest -m live | A few calls |
| T3 Demo run | Real, full scenario | Twice total: record, rehearse | ~50 calls |

Built into the code (Phase 0):
- LLMGateway.complete(role, messages, schema) is the only way agents call a model: cache, then limiter, then provider, then fallback.
- Cache key = hash of (model role, messages, schema). Identical prompts never hit a provider twice, including in replays.
- Token-bucket limiter per provider below the free-tier RPM; exponential backoff on 429.
- Budget guard: persisted daily live-call counter per provider; raises above the cap.
- Per-role call caps in the graph (planning retries, specialist retries, reviewer loops).
- Cheap roles use the small model; only planning and review use the larger one.

Rules for working with Claude Code:
- One phase per session. Start each with /clear; paste only that phase's prompt.
- Tell it which files to touch. No "explore the whole repo".
- pytest -x -q, long output through tail -30.
- Maximum 3 fix attempts per failing test, then stop and report.
- No subagents, live-LLM tests or docker rebuilds unless the prompt says so.
- /compact if a session gets long; commit at every green gate.

## Model routing (config/models.yaml; verify current free-tier names and limits first)

| Role | Primary | Fallback | Why |
| --- | --- | --- | --- |
| Supervisor planning | Gemini Flash-class | Groq 70B-class | Structured-output reliability |
| Specialists | Groq 70B-class | Gemini Flash-class | Fast tool-use loops |
| Reviewer | Gemini Flash-class | Groq 70B-class | Different family from the author = independent check |
| Cheap jobs | Groq 8B-class | Gemini Flash-Lite-class | Saves quota |

## Phases and gates
1. P0 Foundation: scaffold, compose (Postgres, Redis, Chroma), configs, LLM gateway, FakeLLM, CI. Gate: make test green; compose healthy; cache-hit test.
2. P1 Tools: registry, GitHub tools, Docker sandbox, ruff/bandit/pytest/OSV tools, MCP server. Gate: tool tests pass; sandbox blocks network and enforces timeout.
3. P2 Agents and graph: schemas, supervisor, four specialists, reviewer, LangGraph with conditional edges, Redis working memory, FastAPI + Celery. Gate: fake-LLM e2e run; reviewer rejection loop and specialist retry exercised.
4. P3 Long-term memory: Chroma, extraction, retrieval into planning, importance, consolidation, expiry, dashboard, delete. Gate: second similar task retrieves first task's lessons.
5. P4 Human-in-the-loop: triggers, four levels, approval queue, interrupt/resume, chat. Gate: pause, approve resumes, reject re-plans, take-over stands down.
6. P5 Observability: OTel to Postgres, trace API, cost tracking, replay and diff. Gate: trace tree matches execution; replay diff shows the changed step.
7. P6 Frontend: run console, review queue, trace explorer, memory and cost dashboards. Gate: build clean; demo walkable against fake backend.
8. P7 Integration and polish: planted-bug repo, demo script, e2e tests, README, ADRs, one live recording, video. Gate: make demo runs; fresh clone to running under 10 minutes.

## Interview prep

| Question | Answer |
| --- | --- |
| Why a supervisor instead of one agent? | Decomposition, parallelism, per-specialist tool permissions, independent review |
| Runaway loops and cost? | Per-role iteration caps, retry limits in graph edges, gateway budget guard |
| Testing non-deterministic agents? | FakeLLM for logic, cassettes for integration, one live smoke per phase |
| How is the reviewer independent? | Different model family, evidence rules, structured verdicts |
| Worker dies mid-approval? | Postgres checkpointer resumes from the interrupt |
| Memory staleness and leaks? | Importance decay, consolidation, expiry, delete endpoint |
| What changes for production? | Auth and multi-tenancy, GitHub App instead of token, gVisor/Firecracker sandbox, hosted OTel backend, evals on a labelled PR set |
