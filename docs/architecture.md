# Architecture

## Flow
UI -> FastAPI -> Celery worker -> LangGraph graph. Specialists reach the outside world only through the tool registry. Every model call goes through the LLM gateway. The reviewer can send work back. Low confidence, repeated failure or a sensitive step pauses at the human gate, which appears in the React review queue. OpenTelemetry spans from each agent step, tool call and memory lookup feed the trace explorer.

```mermaid
flowchart TD
  UI[React UI] --> API[FastAPI REST + SSE]
  API --> W[Celery worker]
  W --> G
  subgraph G[LangGraph state machine]
    S[Supervisor] --> SP[Specialists]
    SP --> R[Reviewer]
    R -->|rejected: feedback| SP
    R --> H[Human gate]
  end
  G --> T[Tool registry + MCP server]
  G --> L[LLM gateway: Gemini, Groq, cache, limiter, budget]
  G --> M[(Redis, ChromaDB, Postgres)]
  G -.spans.-> O[OpenTelemetry -> Postgres -> trace explorer]
```

## Graph
intake -> plan -> dispatch (parallel for independent subtasks, honouring depends_on) -> review -> synthesize -> deliver.
Conditional edges:
- Specialist failure: retry with an alternate-approach hint (max 2).
- Reviewer rejection: back to the specialist with feedback (max 2).
- Low plan confidence, repeated failure, sensitive operation, low reviewer score, explicit request: escalate to the human gate.

## Agents
- Supervisor: plan(task, retrieved_memories) with structured output and DAG validation; synthesize(results).
- Specialists: code_analysis, security, test_execution, fix_writer. Bounded tool-use loops, restricted to permitted tools. Prompts in agents/prompts/*.md.
- Reviewer: approves only if every finding cites file+line from real tool output, fixes have passing tests, and no claim lacks evidence.

## Tools
Registry entries have name, description, input/output schemas, allowed agents, rate limit. Every call is logged (inputs, outputs, latency, success) and traced. Tools: GitHub (get_pr, get_pr_diff, list_pr_files, get_file, post_review_comment, push_fix_commit), sandboxed run, ruff, bandit, pytest, OSV dependency audit. No merge or deploy tool exists. The registry is also served over MCP.

## Memory
- Short-term (Redis): per task_id; plan, subtask outputs, intermediates, error log; TTL and cleared on completion.
- Long-term (ChromaDB, local embeddings): task_memories, repo_facts, user_prefs. Metadata: user_id, repo, type, created_at, importance, access_count, last_accessed.
- Extraction after completion: request, approach that worked/failed, tools used, repo conventions, observed preferences, human accept/reject of fixes.
- Retrieval before planning: similar past plans, what worked, what failed, preferences, facts, injected into the planning prompt.
- Management: importance score (access, recency, human feedback), consolidation of near-duplicates, expiry by decay, dashboard, delete endpoint.

## Human-in-the-loop
Levels: notify (proceed and inform), approve_action (confirm next step), approve_plan (review whole plan before work), take_over (human supplies output, agents stand down).
Defaults (config/policies.yaml): post_review_comment = notify; push_fix_commit = approve_action; production-labelled repo = approve_plan; repeated failure or any merge request = take_over.
Escalation uses LangGraph interrupt() with a packaged context (task, plan, completed steps, current step, proposed action and reasoning, relevant memories and past decisions). Decisions: approve, modify, reject (re-plan with feedback), take_over. A chat endpoint answers clarifying questions without changing state.

## Observability
OpenTelemetry spans for planning, specialist steps, tool calls, reviewer evaluations, memory retrievals (with memory ids) and escalations. Attributes: task_id, agent, role, model, tokens, notional cost, latency, status. Full prompts/responses live in llm_calls, referenced by id. Replay re-executes from any step with overridden inputs through the gateway cache and diffs against the original.

## Data model
Tables: tasks, plan_steps, llm_calls (model, tokens, latency, notional cost, cache hit), tool_calls, approvals, spans, memory_meta (importance, access count, last accessed), replays, plus LangGraph checkpoint tables.

## Stack
Python 3.11+, FastAPI, Pydantic v2, LangGraph, LiteLLM (Gemini + Groq), Redis, ChromaDB, PostgreSQL, Celery, Docker sandbox, OpenTelemetry, React 18 + Vite + TypeScript + Tailwind + TanStack Query + React Flow + Recharts, uv, ruff, mypy, pytest, GitHub Actions, docker-compose.

## Repo layout
```text
conductor/
  CLAUDE.md  README.md  docker-compose.yml  Makefile  pyproject.toml
  config/    models.yaml  policies.yaml  prices.yaml
  docs/      architecture.md  decisions.md  api.md  demo.md
  src/conductor/
    llm/ agents/ graph/ tools/ memory/ hitl/ obs/ api/ worker/ db/
  sandbox/Dockerfile
  web/
  tests/     unit/ integration/ e2e/ live/
  demo/      seed_target_repo.py  run_demo.py
```
