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
