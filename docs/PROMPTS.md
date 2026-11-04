# Claude Code Prompts (P0 to P7)

Paste one prompt per fresh session (`/clear` first). Each opens by reading CLAUDE.md, states its scope, and ends at a gate. Do not skip a gate: later phases assume it. Put CLAUDE.md at the repo root before P0.

---

## P0 Foundation

```text
Read CLAUDE.md if it exists (create it from the template I provide if not). Phase 0: Foundation. Scope: repo scaffold and LLM gateway only. Do not build agents or tools.

Build:
1. pyproject.toml (uv), src/conductor package skeleton per the layout, Makefile (up, down, test, live, demo), pre-commit with ruff, .env.example, .gitignore, GitHub Actions CI that runs `make test` with no live LLM.
2. docker-compose.yml: postgres:16, redis:7, chromadb, each with healthchecks. Leave api, worker, web as commented placeholders.
3. config/models.yaml: roles (planner, specialist, reviewer, cheap) mapped to primary and fallback models across Gemini and Groq, plus per-provider requests-per-minute caps and a daily live-call budget. config/policies.yaml: skeleton for escalation thresholds and the four approval levels (notify, approve_action, approve_plan, take_over). config/prices.yaml: notional USD per 1M tokens per model.
4. src/conductor/llm/:
   - gateway.py: LLMGateway.complete(role, messages, schema=None) built on LiteLLM. Order: cache lookup, limiter, provider call, fallback on error or 429, optional Pydantic schema validation with ONE retry on invalid JSON. Returns text or model plus usage (tokens, latency, model, cache_hit).
   - cache.py: SQLite disk cache keyed by hash(role, model, messages, schema).
   - limiter.py: async token bucket per provider with backoff.
   - budget guard: persisted daily counter of live calls per provider; raise BudgetExceeded above the cap.
   - fake.py: FakeLLM with scripted responses per role, same interface, used by all tests by default.
5. tests/unit/llm: cache hit skips provider; limiter spaces calls; budget guard raises; fallback on provider error; schema retry on bad JSON; live tests marked `live` and skipped without LIVE_LLM=1.
6. docs/architecture.md skeleton and docs/decisions.md with ADR-001 LangGraph interrupt over hand-rolled pause state, ADR-002 local embeddings, ADR-003 gateway plus cache plus budget guard.

Gate: `make test` green, `docker compose up -d` healthy, no live calls made. Commit: "feat: foundation and llm gateway". Report in 5 lines.
```

---

## P1 Tools

```text
Read CLAUDE.md. Phase 1: Tools. Scope: src/conductor/tools/, sandbox/Dockerfile, tests/unit/tools, a stub tracer interface in obs/tracing.py. No agents yet.

Build:
- registry.py: Tool model (name, description, input_schema, output_schema, allowed_agents, rate_limit). `invoke(agent, tool, args)` enforces permission and rate limit, validates input and output with Pydantic, writes a tool_calls row (inputs, outputs, latency, success) and opens a tracer span. Registration via decorator.
- github.py (httpx, token from env, DRY_RUN flag): get_pr, get_pr_diff, list_pr_files, get_file, post_review_comment, push_fix_commit. There is NO merge or deploy tool, by design.
- sandbox.py + sandbox/Dockerfile: run_in_sandbox(snapshot_dir, cmd, timeout) using the docker SDK: network none, read-only rootfs, tmpfs /tmp, 256m memory, 0.5 cpu, pids limit, non-root user, hard kill on timeout, output truncated. Image has ruff, bandit, pytest preinstalled.
- linters.py: run_ruff, run_bandit, run_tests returning structured findings (file, line, rule, severity, message), never raw text.
- deps.py: audit_requirements() querying the OSV API from the host (not the sandbox) for known vulnerabilities.
- mcp_server.py: expose registry tools through the official MCP Python SDK (FastMCP); one command to start.

Tests: permission denied, rate limit hit, schema failure, log row written, GitHub tools against recorded fixtures (respx), sandbox tests marked `docker` (network blocked, timeout kills, rootfs write fails). Build the sandbox image once now; that is the only image build allowed.

Gate: `make test` green including docker-marked tests. Commit: "feat: tool registry, github, sandbox, linters, mcp". Report in 5 lines.
```

---

## P2 Agents and graph

```text
Read CLAUDE.md. Phase 2: Agents and graph. Scope: agents/, graph/, memory/working.py, api/, worker/, db models for tasks, plan_steps, llm_calls. All tests use FakeLLM.

Build:
- agents/schemas.py (Pydantic v2): Subtask(id, description, specialist, depends_on, required_inputs, expected_output_format, complexity); ExecutionPlan(steps, confidence, rationale); Finding(file, line, rule, severity, evidence); SpecialistResult(subtask_id, output, findings, tool_summary, confidence); ReviewVerdict(subtask_id, approved, score, issues, feedback).
- supervisor.py: plan(task, retrieved_memories) with structured output; validate the dependency graph (no cycles, valid ids, valid specialist); synthesize(results) into a final review report.
- specialists/: code_analysis, security, test_execution, fix_writer. Each is a bounded tool-use loop (max iterations in config) restricted to its permitted tools. Prompts live in agents/prompts/*.md, not inline.
- reviewer.py: approves only if every finding cites file+line from real tool output, proposed fixes have passing tests, and no claim lacks evidence.
- graph/: LangGraph StateGraph: intake, plan, dispatch (Send fan-out for independent subtasks, honouring depends_on), review, then conditional edges: specialist failure -> retry with an alternate-approach hint (max 2); reviewer rejection -> back to the specialist with feedback (max 2); low plan confidence -> escalate node (stub that marks needs_human; real HITL arrives in P4); otherwise synthesize -> deliver. Postgres checkpointer.
- memory/working.py: Redis store scoped by task_id holding plan, subtask outputs, intermediates, error log; TTL plus explicit clear on completion.
- api: POST /tasks, GET /tasks/{id}, GET /tasks/{id}/events (SSE). worker: Celery task that runs the graph.

Tests: happy path; specialist fails then succeeds; reviewer rejects once then approves; cyclic plan rejected; independent subtasks run concurrently; working memory cleared on completion.

Gate: fake-LLM end-to-end run via the API completes and persists. Commit: "feat: agents, graph, working memory, api". Report in 5 lines.
```

---

## P3 Long-term memory

```text
Read CLAUDE.md. Phase 3: Long-term memory. Scope: memory/longterm.py, retrieval.py, manager.py, api/routes/memory.py, hooks into supervisor.plan and post-task. Local embeddings only.

Build:
- longterm.py: ChromaDB collections task_memories, repo_facts, user_prefs. Local embedding model (Chroma default or all-MiniLM-L6-v2). Metadata: user_id, repo, type, created_at, importance, access_count, last_accessed.
- Extraction (cheap role, structured output), run after task completion: what was asked, approach that worked and what failed, tools used, repo conventions and facts, observed preferences, and whether the human accepted or rejected each proposed fix.
- retrieval.py: given task, repo, user return top-k grouped as similar past plans, what worked, what failed, preferences, facts. Format as a prompt section injected into planning. Increment access_count on use.
- manager.py: importance score from access count, recency and human-feedback weight; consolidation merges near-duplicates above a similarity threshold into a summary via the cheap role, keeping links to sources; expiry decays score and deletes below threshold; schedule as a Celery beat job.
- API: GET /memory?user_id (grouped dashboard payload), DELETE /memory/{user_id} hard-deletes from Chroma and memory_meta.

Tests: second similar task retrieves the first task's lessons and the plan prompt contains them; importance rises with access; consolidation reduces count and keeps provenance; expiry removes stale items; delete endpoint leaves nothing for that user.

Gate: `make test` green. Commit: "feat: long-term memory". Report in 5 lines.
```

---

## P4 Human-in-the-loop

```text
Read CLAUDE.md. Phase 4: Human-in-the-loop. Scope: hitl/, api/routes/approvals.py, graph escalate node, config/policies.yaml. FakeLLM only.

Build:
- triggers.py: evaluate(state) against policies.yaml. Triggers: plan confidence below threshold; a specialist failed twice on the same subtask; a sensitive operation (push_fix_commit, comments on a production-labelled repo, anything deleting data or contacting externally); reviewer score below threshold; user explicitly requested review.
- levels.py: notify (proceed and inform), approve_action (confirm next step), approve_plan (human reviews the whole plan before any work), take_over (human supplies the output, agents stand down). Defaults in policies.yaml: post_review_comment = notify; push_fix_commit = approve_action; production-labelled repo = approve_plan; repeated failure or any merge request = take_over.
- queue.py: on escalation call LangGraph interrupt() with a packaged context (original task, plan, completed steps, current step, proposed action with reasoning, relevant memories and similar past decisions). Write an approvals row, emit an SSE event.
- API: GET /approvals?status=pending; POST /approvals/{id}/decision with approve | modify(edited action) | reject(reason) | take_over(output). Resume with Command(resume=...). Reject routes back to the planner with the human's feedback. Modify applies the edited action.
- POST /approvals/{id}/chat: answers clarifying questions from the approval context; stores history; never changes task state.
- Store every human decision as memory feedback for P3 importance scoring.

Tests: each trigger fires on the right condition; each level behaves as defined; approve resumes from the checkpoint even after the worker restarts; reject re-plans; modify applies; take_over ends agent work; chat leaves task state untouched.

Gate: `make test` green. Commit: "feat: human-in-the-loop". Report in 5 lines.
```

---

## P5 Observability and replay

```text
Read CLAUDE.md. Phase 5: Observability. Scope: obs/, api/routes/traces.py, api/routes/metrics.py, api/routes/replays.py. FakeLLM only.

Build:
- tracing.py: OpenTelemetry tracer provider and helpers. Spans for supervisor planning, each specialist step, each tool call, reviewer evaluation, memory retrieval (attribute lists the memory ids used), escalation events and their resolutions. Attributes: task_id, agent, role, model, tokens in/out, notional cost, latency, status. Full prompts and responses stay in llm_calls, referenced by id, to keep spans small.
- exporter.py: custom SpanExporter that batch-writes to the Postgres spans table.
- GET /traces/{task_id}: span tree with status, latency, cost, error per node.
- cost.py: per task totals (tokens by agent and model, tool calls, wall-clock, human review time, notional cost via prices.yaml). GET /metrics/cost: cost per task type, most expensive agents, tool usage patterns, escalation-rate trend.
- replay.py: load a past task, step through its decisions, let the caller override the input at any step (a different plan, a changed tool result), re-execute from that step through the gateway cache so unchanged prompts are free, store the run in replays, and diff against the original (changed steps, outputs, cost). POST /replays and GET /replays/{id}/diff.

Tests: trace tree matches the executed graph; spans carry the required attributes; cost totals match llm_calls; replay with a modified input diverges and the diff reports exactly that step; replay with no change is identical and costs zero live calls.

Gate: `make test` green. Commit: "feat: tracing, cost, replay". Report in 5 lines.
```

---

## P6 Frontend

```text
Read CLAUDE.md. Phase 6: Frontend. Scope: web/ plus a backend demo-mode flag. Zero live LLM calls.

First, backend: add env CONDUCTOR_FAKE_LLM=1 that runs the graph with a scripted FakeLLM demo scenario (demo/fake_script.py) so the UI can be built and shown without quota.

Then web/: React 18, Vite, TypeScript, Tailwind, TanStack Query, React Flow, Recharts. Generate a typed API client from the FastAPI OpenAPI schema (openapi-typescript). Pages:
1. Run console: start a review (repo, PR number), live progress over SSE, plan shown as a graph of subtasks.
2. Review queue: pending approvals; detail view with task context, plan progress, the proposed action and its reasoning, relevant memories and past similar decisions; buttons approve, modify, reject, take over; a chat panel to question the agent before deciding.
3. Trace explorer: React Flow tree of the run. Each node shows agent, decision, tools called, latency, cost, errors. Colour by status (success, warning, failure, escalated). Clicking a node opens a drawer with the full prompt and response.
4. Memory dashboard: what the system remembers per user and repo, with a confirmed delete.
5. Cost dashboard: Recharts charts for cost per task type, most expensive agents, tool usage, escalation-rate trend.
Design: intentional and polished, not a default template. Light and dark themes, consistent spacing and type scale, loading, empty and error states on every page, keyboard-accessible, responsive down to tablet.
Add the web service to docker-compose (static build behind nginx).

Tests: Vitest for the decision panel and the status-colour mapping. No browser e2e.

Gate: `npm run build` and `tsc --noEmit` clean; with CONDUCTOR_FAKE_LLM=1 the full demo can be walked through in the UI. Commit: "feat: frontend". Report in 5 lines.
```

---

## P7 Integration and polish

```text
Read CLAUDE.md. Phase 7: Integration and polish. Scope: demo/, tests/e2e, docs/, README.md, docker-compose.yml. FakeLLM and cassettes only, except where stated.

Build:
- demo/seed_target_repo.py: idempotently creates a small Python web app repo on my GitHub account (token with repo scope, throwaway repo only) containing planted issues: a SQL injection from string-formatted queries, a hardcoded secret, a pinned dependency with a known CVE, a failing test, and lint violations. Create two PRs: PR 1 a simple SQL-injection fix scenario (populates memory), PR 2 the main scenario.
- demo/run_demo.py and `make demo`, with --fake (scripted, zero quota) and --live modes. Flow: review PR 1 and store lessons; review PR 2: supervisor decomposes, specialists run in parallel, the first proposed fix breaks a test, the reviewer rejects it, memory recalls the PR 1 fix pattern, a second fix passes, the push step pauses for human approval, approve, memory saves lessons.
- tests/e2e per the spec: plans are valid; specialists use their tools correctly; the reviewer catches deliberately bad output; memory retrieval improves planning on a repeat task; escalation fires at the right moments; the system recovers from an agent failure.
- docker-compose: api, worker, beat, redis, postgres, chroma, web, all healthy with one `make up`.
- README.md: one-paragraph pitch, architecture diagram (Mermaid), screenshot placeholders, 5-minute quickstart, feature list mapped to the spec, design decisions and trade-offs, test tiers, limitations, roadmap.
- docs/: finish architecture.md, ADRs, api.md generated from OpenAPI.
- Add a scripts/fresh_clone_check.sh that clones, brings up the stack and runs the fake demo.

Then STOP and tell me to run ONE live recording: `LIVE_LLM=1 make demo ARGS=--live` to populate the cassette cache, which you commit. Do not run it yourself.

Gate: `make demo ARGS=--fake` runs end to end; `make test` green; fresh_clone_check passes. Commit: "feat: demo, e2e, docs". Report in 5 lines.
```
