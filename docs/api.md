# API

Generated from the FastAPI OpenAPI schema by `make docs`; do not edit by hand.
Every body is a Pydantic v2 model. The React client in `web/` is generated from the same
schema (`npm run api:types`). Interactive docs: `/docs` on the running API.

| Method | Path | Request body | Response | Notes |
| --- | --- | --- | --- | --- |
| GET | `/memory` | - | `MemoryDashboard` | query: user_id; returns 200 |
| DELETE | `/memory/{user_id}` | - | `object` | returns 200 |
| GET | `/traces/{task_id}` | - | `TraceTree` | returns 200 |
| GET | `/traces/{task_id}/spans/{span_id}` | - | `SpanDetail` | returns 200 |
| GET | `/metrics/cost` | - | `CostReport` | query: days; returns 200 |
| GET | `/metrics/tasks/{task_id}` | - | `TaskCost` | returns 200 |
| POST | `/replays` | `ReplayIn` | `ReplayOut` | returns 201 |
| GET | `/replays/steps` | - | list of `Step` | query: task_id; returns 200 |
| GET | `/replays/{replay_id}` | - | `ReplayOut` | returns 200 |
| GET | `/replays/{replay_id}/diff` | - | `ReplayDiff` | returns 200 |
| GET | `/approvals` | - | list of `ApprovalOut` | query: status; returns 200 |
| GET | `/approvals/{approval_id}` | - | `ApprovalOut` | returns 200 |
| POST | `/approvals/{approval_id}/decision` | `object` | `object` | returns 202 |
| POST | `/approvals/{approval_id}/chat` | `ChatIn` | `ChatOut` | returns 200 |
| GET | `/health` | - | `object` | returns 200 |
| POST | `/tasks` | `TaskCreate` | `object` | returns 202 |
| GET | `/tasks` | - | list of `TaskSummary` | query: limit; returns 200 |
| GET | `/tasks/{task_id}` | - | `TaskOut` | returns 200 |
| GET | `/tasks/{task_id}/events` | - | server-sent events | returns 200 |

## Models

- **AgentCost**: `agent`, `calls`, `tokens_in`, `tokens_out`, `cost_usd`
- **ApprovalOut**: `id`, `task_id`, `kind`, `level`, `status`, `context`, `decision`
- **Bucket**: `calls`?, `tokens_in`?, `tokens_out`?, `cost_usd`?
- **ChatIn**: `question`
- **ChatOut**: `answer`, `history`
- **CostReport**: `per_task_type`, `top_agents`, `tool_usage`, `escalation_trend`
- **EscalationDay**: `day`, `tasks`, `escalated`, `rate`
- **LLMDetail**: `role`, `model`, `schema_name`, `messages`, `response`, `cache_hit`
- **MemoryDashboard**: `user_id`, `counts`, `groups`
- **MemoryItem**: `id`, `collection`, `text`, `user_id`, `repo`?, `type`, `created_at`, `last_accessed`, `importance`, `access_count`?, `feedback`?, `sources`?
- **Override**: `step`, `value`
- **ReplayDiff**: `replay_id`, `source_task_id`, `overridden_steps`, `diverged`, `changed_steps`, `added_steps`, `removed_steps`, `unchanged_steps`, `live_calls`, `cost`, `status`, `report_changed`
- **ReplayIn**: `task_id`, `overrides`?
- **ReplayOut**: `id`, `source_task_id`, `status`, `live_calls`, `cost_usd`, `error`, `result`
- **SpanDetail**: `span_id`, `name`, `llm`?, `tool`?
- **Step**: `index`, `kind`, `agent`, `name`, `subtask`, `ordinal`, `key`, `input_preview`, `raw_input`?, `output`, `error`?, `model`?, `tokens_in`?, `tokens_out`?, `cost_usd`?
- **StepChange**: `identity`, `kind`, `agent`, `name`, `before`?, `after`?
- **StepOut**: `id`, `description`, `specialist`, `depends_on`, `status`
- **TaskCost**: `task_id`, `llm_calls`, `tokens_in`, `tokens_out`, `cost_usd`, `by_agent`, `by_model`, `tool_calls`, `tool_calls_by_tool`, `wall_clock_seconds`, `human_review_seconds`
- **TaskCreate**: `request`, `repo`?, `pr_number`?, `snapshot_dir`?, `user_id`?, `labels`?, `head_ref`?, `review_required`?
- **TaskOut**: `id`, `request`, `status`, `needs_human`, `escalation_reason`, `report`, `error`, `steps`
- **TaskSummary**: `id`, `status`, `request`, `created_at`
- **ToolDetail**: `tool`, `inputs`, `outputs`, `success`, `error`
- **ToolUsage**: `tool`, `calls`, `errors`, `avg_latency_ms`
- **TraceNode**: `span_id`, `name`, `agent`, `role`, `model`, `status`, `error`, `duration_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `subtree_cost_usd`, `llm_call_id`?, `attributes`, `children`?
- **TraceTree**: `task_id`, `span_count`, `status`, `total_cost_usd`, `roots`
- **TypeCost**: `task_type`, `tasks`, `cost_usd`, `avg_cost_usd`, `tokens`
