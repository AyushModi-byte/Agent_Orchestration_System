# Conductor

Conductor is a multi-agent reviewer for pull requests. A supervisor breaks a review into dependency-ordered subtasks, four tool-using specialists (code analysis, security, test execution, fix writing) do the work in parallel, and a reviewer agent refuses any claim that does not cite real tool output. Sensitive steps pause for a human, past reviews teach it what to do next time, every decision is traced and priced, and **it cannot merge or deploy by design**. It runs on free-tier Gemini and Groq, and the whole test suite and demo run with zero quota.

![Run console](docs/img/run-console.png) <!-- placeholder: capture with `make demo-ui` -->
![Trace explorer](docs/img/trace-explorer.png) <!-- placeholder -->
![Review queue](docs/img/review-queue.png) <!-- placeholder -->

## Architecture

```mermaid
flowchart TD
  UI[React UI] -->|REST + SSE| API[FastAPI]
  API --> W[Celery worker]
  W --> G
  subgraph G[LangGraph state machine]
    S[Supervisor: plan, synthesize] --> SP[Specialists in parallel]
    SP --> R[Reviewer: evidence rules + LLM judge]
    R -->|rejected, with feedback| SP
    R --> H{Human gate}
  end
  G --> T[Tool registry + MCP server]
  T --> GH[GitHub, dry-run by default]
  T --> SB[Docker sandbox: no network, read-only]
  G --> L[LLM gateway: cache, limiter, budget, fallback]
  L --> P[Gemini / Groq]
  G --> M[(Redis working memory, Chroma long-term memory, Postgres)]
  G -.OpenTelemetry spans.-> O[(spans, llm_calls, tool_calls)] --> UI
```

## Metrics

Measured on this repository today (no model quality claims are made from scripted runs):

| What | Value |
| --- | --- |
| Python tests (unit, integration, e2e) | 130+ passing, 4 Docker-only skipped without Docker |
| UI tests (Vitest) | 14 passing |
| Type checking | mypy strict clean on `src/`, `tsc --noEmit` clean |
| Scripted demo | 2 PRs, 6 subtasks, 1 reviewer rejection, 1 human gate, runs in a few seconds |
| Live demo cost, tokens, latency, rejection rate | _filled in after the one live recording (`LIVE_LLM=1 make demo ARGS=--live`)_ |

## Quickstart (5 minutes)

You need Python 3.11+, Node 20+ (for the UI) and, for the full stack, Docker.

```bash
git clone <this repo> && cd conductor
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]" 2>/dev/null || pip install -e . && pip install pytest pytest-asyncio ruff mypy

make demo                    # scripted two-PR scenario, zero quota, no network
make demo-ui                 # API in demo mode + Vite dev server on http://localhost:5173
make up                      # full stack: api, worker, beat, redis, postgres, chroma, web  (Docker)
                             #   UI at http://localhost:8080, API docs at http://localhost:8000/docs
CONDUCTOR_FAKE_LLM=1 make up # same stack, scripted models (no API keys needed)
make test                    # ruff, mypy, pytest, UI build and tests
scripts/fresh_clone_check.sh # clone -> install -> up -> demo, with timing
```

Real models: copy `.env.example` to `.env`, add `GEMINI_API_KEY` and `GROQ_API_KEY`. Nothing calls a provider unless `LIVE_LLM=1`. GitHub writes are dry runs unless `DRY_RUN=0`.

## Features

| Spec | Where |
| --- | --- |
| Supervisor plans a DAG with confidence and rationale | `agents/supervisor.py`, `graph/builder.py` |
| Four specialists with bounded tool loops and per-agent tool permissions | `agents/specialists`, `config/agents.yaml`, `tools/registry.py` |
| Reviewer: evidence rules first, LLM judge second, different model family | `agents/reviewer.py`, `config/models.yaml` |
| Tool registry (schemas, permissions, rate limits, logging, tracing) and MCP server | `tools/` |
| Sandboxed execution: no network, read-only root, limits, timeout | `tools/sandbox.py`, `sandbox/Dockerfile` |
| LLM gateway: cache, rate limiter, daily budget, fallback, schema retry | `llm/` |
| Working memory (Redis) and long-term memory (Chroma) with importance, consolidation, expiry, delete | `memory/` |
| Four human-in-the-loop levels, policy in YAML, interrupt/resume, chat before deciding | `hitl/`, `graph/gates.py`, `config/policies.yaml` |
| Tracing, per-agent cost, deterministic replay with diff | `obs/` |
| UI: run console, review queue, trace explorer, memory, cost | `web/` |
| Demo, planted-bug repo seeder, e2e behaviours | `demo/`, `tests/e2e/` |

## Design decisions and trade-offs

Full records in [docs/decisions.md](docs/decisions.md). The ones that shape the code:

- **No merge, no deploy.** There is no such tool; merging is the human "take over" level. Less autonomy, a whole class of risk removed.
- **One door for model calls.** Cache, limiter, budget and fallback live in the gateway, so free tiers stay predictable and replays are free. Cost: every caller must accept a gateway-shaped interface.
- **LangGraph `interrupt()` for human pauses**, resumed from a checkpoint, even from another worker. Couples us to LangGraph persistence.
- **Evidence before opinion.** The reviewer rejects findings that do not match tool output before spending a model call.
- **Notional pricing.** Free tiers bill nothing, so cost is estimated from `config/prices.yaml`; it ranks things correctly but is not an invoice.
- **Demo mode runs the real graph** on a scripted model, not a UI mock. It shows behaviour, not model quality.

## Test tiers

T0 unit and T1 integration run on FakeLLM and fake tools; e2e covers the showcase and six behaviours (valid plans, correct tool use, the reviewer catching bad output, memory improving a repeat plan, escalation at the right moments, recovery from agent failure); `pytest -m docker` exercises the real sandbox; `pytest -m live` is the only tier that spends quota and needs `LIVE_LLM=1`. CI runs `make test` without it.

## Limitations

- Not yet run against real Postgres checkpoints, a real Chroma server or real MiniLM embeddings in CI; the compose stack is defined but depends on your Docker.
- The worker mounts the host Docker socket for sandboxes (root-equivalent). Fine locally; use gVisor or Firecracker in production.
- Tables are created with `create_all`, not migrations.
- Replay runs inside the API process. The scripted demo shows behaviour, not model quality.
- Memory delete is per user, not per item. Free-tier models are slow and rate limited.

## Roadmap

Alembic migrations; per-item memory edit and delete; GitHub App auth and webhook-triggered reviews; gVisor sandbox; replay as a background job; richer eval suite on recorded cassettes; more specialists (docs, performance).

Docs: [architecture](docs/architecture.md) · [decisions](docs/decisions.md) · [API](docs/api.md) · [demo script](docs/demo.md)
