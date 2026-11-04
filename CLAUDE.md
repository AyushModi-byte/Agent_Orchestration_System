# Conductor: multi-agent code-review and DevOps orchestrator

## What this is
Supervisor plans a PR review, specialists (code analysis, security, test execution, fix/writing) use tools, a reviewer gates outputs, humans approve sensitive actions, memory improves later runs, everything is traced. Python 3.11, FastAPI, LangGraph, Postgres, Redis, ChromaDB, Celery, React. Free-tier LLMs only (Gemini, Groq).

## Hard rules
- NEVER call a real LLM from tests or scripts unless env LIVE_LLM=1. Default is FakeLLM or cached cassettes.
- All model calls go through conductor.llm.gateway.LLMGateway. No direct provider SDK calls elsewhere.
- The system must never merge or deploy. Merge is human-only. No tool may perform it.
- No secrets in code or logs. Read from env (.env is gitignored; .env.example is committed).
- Sandboxed code execution: no network, read-only root fs, memory and CPU limits, timeout. Never run target-repo code on the host.

## Working rules (protect usage limits)
- Work only on the phase named in the prompt. Touch only files in scope.
- Do not explore the whole repo. Read the files you need.
- Run tests with: pytest -x -q  (add | tail -30 for long output). Use --lf to rerun failures.
- Maximum 3 attempts to fix one failing test. After that stop and report what failed and why.
- No subagents. No docker image rebuilds unless asked. No live LLM calls unless asked.
- Commit at each green gate with a conventional-commit message.

## Code standards
- Type hints everywhere; mypy clean on src/. ruff format and ruff check clean.
- Pydantic v2 models for every agent input/output and every API body.
- Every agent step and tool call emits an OpenTelemetry span and a DB row.
- Small modules, no god files. Docstrings explain why, not what.
- Every new module gets unit tests using FakeLLM. Integration tests use cassettes.

## Commands
- make up / make down: docker compose stack
- make test: ruff + mypy + pytest (T0 and T1)
- make live: LIVE_LLM=1 pytest -m live  (spends quota; ask first)
- make demo: run the showcase scenario

## Architecture pointers
See docs/architecture.md and config/*.yaml. Escalation triggers and approval levels live in config/policies.yaml, not in code.
