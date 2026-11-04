# Demo

## Scenario
Target: a small Python web app repo on your GitHub account with planted issues: SQL injection from string-formatted queries, a hardcoded secret, a pinned dependency with a known CVE, a failing test, lint violations.
- PR 1: simple SQL-injection fix scenario. Reviewed first; lessons stored in memory.
- PR 2: main scenario. Supervisor decomposes; specialists run in parallel; the first proposed fix breaks a test; the reviewer rejects it; memory recalls the PR 1 fix pattern; the second fix passes; the push step pauses for human approval; approve; memory saves lessons.

Modes: `make demo ARGS=--fake` (scripted, zero quota) and `LIVE_LLM=1 make demo ARGS=--live` (run once to populate the cache).

## Video script (under 5 minutes)
1. Architecture diagram, 20 seconds: supervisor, specialists, reviewer, memory, human gate.
2. Start the PR 2 review in the UI; show the plan graph and parallel specialists.
3. Show the reviewer rejecting the first fix because it broke a test.
4. Show memory retrieval influencing the second plan (PR 1 lesson in the prompt).
5. Approve the push in the review queue; use the chat panel once.
6. Open the trace explorer, then replay with one changed input and show the diff.
7. Close on the cost dashboard and one-command `make up`.

## Resume bullets (adjust to real results)
- Built a multi-agent PR-review orchestrator in Python with LangGraph, FastAPI and React: a supervisor decomposes tasks into dependency-ordered subtasks for four tool-using specialists, with a reviewer agent gating every output.
- Implemented tiered human-in-the-loop control (notify, approve action, approve plan, take over) using LangGraph interrupts and a Postgres checkpointer; the system cannot merge or deploy by design.
- Added short-term (Redis) and semantic long-term (ChromaDB) memory with importance scoring, consolidation, expiry and a user-data delete endpoint.
- Instrumented every agent decision with OpenTelemetry, a trace explorer UI, per-agent cost tracking and deterministic replay; built an LLM gateway with caching, rate limiting, fallback and a daily call budget on free-tier Gemini and Groq.
- Isolated code execution in network-less Docker sandboxes; test suite runs on fake and recorded LLMs so CI costs nothing.
