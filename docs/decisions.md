# Architecture Decision Records

## ADR-001: LangGraph interrupt() over hand-rolled pause state
Status: accepted. Human pauses use LangGraph interrupt() with a Postgres checkpointer. Execution resumes from the exact checkpoint, including after a worker restart, with no custom state machine to maintain. Trade-off: couples us to LangGraph's persistence model.

## ADR-002: Local embeddings for long-term memory
Status: accepted. ChromaDB with a local MiniLM-class model. Zero API quota, no network dependency, deterministic in tests. Trade-off: lower embedding quality than hosted models, acceptable for short engineering memories.

## ADR-003: LLM gateway with cache, limiter and budget guard
Status: accepted. One entry point for all model calls: disk cache, per-provider token bucket, fallback, daily live-call budget. Keeps free-tier use predictable, makes replays free, and lets tests run on FakeLLM or recorded responses.

## ADR-004: Celery runs whole executions; LangGraph handles parallelism
Status: accepted. Celery workers run task executions and sandbox jobs. Parallel specialists use LangGraph fan-out. Fewer moving parts than one Celery task per specialist, same observable behaviour.

## ADR-005: No merge or deploy capability
Status: accepted. The system cannot merge or deploy. Merge is the human-only "take over" level. Removes a whole class of unsafe-autonomy risk.

## ADR-006: Notional cost tracking
Status: accepted. Free tiers bill nothing, so cost is computed from config/prices.yaml. Preserves per-agent and per-task cost analytics that would matter on paid models.

## ADR-007: Docker sibling-container sandbox
Status: accepted. Target-repo code runs only in containers with no network, read-only root, tmpfs /tmp, memory/CPU/pid limits, non-root user and hard timeout. Production would use gVisor or Firecracker.

## ADR-008: Reviewer from a different model family
Status: accepted. The reviewer uses a different provider/model family than the specialist it checks, plus evidence rules and structured verdicts, to reduce correlated errors.

## ADR-009: Replay re-executes the graph over recorded answers
Status: accepted. A replay runs the real graph with an LLM and tool layer that serve each call from the original recording; an override changes one step and anything downstream that no longer matches goes through the gateway. Steps are matched by identity (kind, agent, name, subtask, ordinal), so a diff reports exactly what changed. Write tools are forced to dry-run. Trade-off: replay needs deterministic prompts, which cost us two small changes (no random run ids in reviewer prompts, sorted synthesis payloads).

## ADR-010: A scripted demo mode instead of mocks in the UI
Status: accepted. `CONDUCTOR_FAKE_LLM=1` runs the unchanged graph, API and tracing on a scripted model and canned tools. The UI and the demo are therefore exercised against the real code paths at zero quota, and a UI bug cannot hide behind a front-end mock. Trade-off: the script is hand-written, so it demonstrates behaviour rather than model quality; quality claims need the live recording.

## ADR-011: Notional prices and per-call rows over provider billing
Status: accepted. Cost comes from `llm_calls` rows priced by `config/prices.yaml`; cache hits cost zero. Spans reference the row by id so they stay small. Trade-off: numbers are estimates on free tiers; they rank agents and tasks correctly but are not an invoice.

## ADR-012: Cassettes are the gateway's own disk cache
Status: accepted. The one live recording fills `demo/cassettes/demo.llmcache`, the same cache the gateway always consults first, so a recorded demo replays through production code with no special test double. Trade-off: any prompt change invalidates the affected entries; re-record after editing prompts.

## ADR-013: Memory recall is advisory
Status: accepted. A failed or empty memory lookup returns "nothing remembered" instead of failing the run (Chroma can transiently error right after writes). Post-task extraction failures are recorded as events and never turn a finished review into a failure.

