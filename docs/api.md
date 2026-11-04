# API

| Endpoint | Purpose |
| --- | --- |
| POST /tasks | Start a review (repo, PR number, approval policy) |
| GET /tasks/{id} | Task state |
| GET /tasks/{id}/events | Live progress (SSE) |
| GET /approvals?status=pending | Review queue |
| POST /approvals/{id}/decision | approve, modify, reject, take_over |
| POST /approvals/{id}/chat | Ask the agent questions before deciding (does not change task state) |
| GET /traces/{task_id} | Span tree with status, latency, cost, error per node |
| POST /replays | Re-run from a step with overridden inputs |
| GET /replays/{id}/diff | Compare replay to original |
| GET /memory?user_id= | Memory dashboard (grouped by type and repo) |
| DELETE /memory/{user_id} | Hard-delete all memory for a user |
| GET /metrics/cost | Cost per task type, expensive agents, tool usage, escalation-rate trend |
| GET /health | Liveness |

All request and response bodies are Pydantic v2 models; the OpenAPI schema generates the typed React client. This file is regenerated from OpenAPI in Phase 7.
