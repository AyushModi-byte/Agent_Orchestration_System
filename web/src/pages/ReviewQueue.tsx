import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useApproval, useApprovals, useDecide } from "../api/hooks";
import { asContext } from "../api/types";
import ChatPanel from "../components/ChatPanel";
import DecisionPanel from "../components/DecisionPanel";
import { Badge, Card, Code, EmptyState, ErrorState, LoadingState, Mono, PageHeader, StatusBadge } from "../components/ui";
import { label } from "../lib/status";

const TABS = [
  { id: "pending", label: "Pending" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

function List({ tab, current }: { tab: string; current?: string }) {
  const q = useApprovals(tab === "all" ? undefined : tab === "resolved" ? "resolved" : "pending");
  if (q.isLoading) return <LoadingState rows={3} label="Loading approvals" />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  if (!q.data?.length)
    return (
      <EmptyState
        title={tab === "pending" ? "Nothing waiting for you" : "No approvals here yet"}
        hint="When an agent needs a human decision, it shows up here with its reasoning."
      />
    );
  return (
    <ul className="space-y-2">
      {q.data.map((a) => {
        const ctx = asContext(a.context);
        return (
          <li key={a.id}>
            <Link
              to={`/queue/${a.id}`}
              aria-current={a.id === current ? "page" : undefined}
              className={`block rounded-xl border p-3 transition-colors hover:bg-surface-2 ${a.id === current ? "border-accent bg-accent-soft" : "border-line bg-surface"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">{label(a.kind)} approval</span>
                <Badge tone={a.status === "pending" ? "escalated" : "neutral"}>{label(a.level)}</Badge>
              </div>
              <p className="mt-1 line-clamp-2 text-[0.8125rem] text-ink-2">
                {ctx.proposed_action?.summary ?? ctx.task?.request ?? a.task_id}
              </p>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-[0.75rem] font-semibold tracking-wide text-ink-3 uppercase">{title}</h3>
      {children}
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const q = useApproval(id);
  const decide = useDecide(id);
  if (q.isLoading) return <LoadingState rows={4} label="Loading approval" />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const a = q.data!;
  const ctx = asContext(a.context);
  const pending = a.status === "pending" && !decide.isSuccess;
  const prop = ctx.proposed_action;
  const done = new Set(ctx.completed_steps.map((s) => s.id));
  const steps = ctx.plan?.steps ?? [];

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold text-ink">{label(ctx.kind)} approval</h2>
          <Badge tone="escalated">{label(ctx.level)}</Badge>
          <StatusBadge status={a.status} tone={a.status === "pending" ? "escalated" : "success"} />
          <span className="ml-auto flex gap-3 text-sm">
            <Link className="font-medium text-accent hover:underline" to={`/run/${a.task_id}`}>
              Run
            </Link>
            <Link className="font-medium text-accent hover:underline" to={`/traces/${a.task_id}`}>
              Trace
            </Link>
          </span>
        </div>
        <div className="mt-4 grid gap-5 md:grid-cols-2">
          <Section title="Task">
            <p className="text-sm text-ink">{ctx.task?.request}</p>
            <p className="mt-1 text-[0.8125rem] text-ink-2">
              {ctx.task?.repo && <Mono>{ctx.task.repo}</Mono>} {ctx.task?.pr_number != null && <>PR #{ctx.task.pr_number}</>}
            </p>
          </Section>
          <Section title="Why a human is needed">
            {ctx.triggers.length === 0 ? (
              <p className="text-sm text-ink-3">No specific trigger recorded.</p>
            ) : (
              <ul className="space-y-1 text-sm text-ink-2">
                {ctx.triggers.map((t, i) => (
                  <li key={i}>
                    <Mono className="text-escalated">{t.name}</Mono> — {t.reason}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </Card>

      <Card title="Plan progress" aside={<span className="text-[0.75rem] text-ink-3">{done.size} of {steps.length} approved</span>}>
        {steps.length === 0 ? (
          <p className="text-sm text-ink-3">{ctx.plan_error ?? "There is no plan yet."}</p>
        ) : (
          <ol className="space-y-1.5">
            {steps.map((s) => (
              <li key={s.id} className="flex items-center gap-2.5 text-sm">
                <span aria-hidden className={`grid size-5 place-items-center rounded-full text-[0.7rem] ${done.has(s.id) ? "bg-success-soft text-success" : "bg-surface-2 text-ink-3"}`}>
                  {done.has(s.id) ? "✓" : "·"}
                </span>
                <Mono className="text-ink-3">{s.id}</Mono>
                <span className="font-medium text-ink">{label(s.specialist)}</span>
                <span className="truncate text-ink-2">{s.description}</span>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card title="Proposed action">
        {prop ? (
          <div className="space-y-3">
            <p className="text-sm font-medium text-ink">{prop.summary}</p>
            {prop.reasoning && (
              <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-ink-2">
                <span className="font-medium text-ink">Reasoning: </span>
                {prop.reasoning}
              </p>
            )}
            {prop.tool && (
              <p className="text-[0.8125rem] text-ink-2">
                <Mono>{prop.tool}</Mono> by <Mono>{prop.agent}</Mono> (writes are dry runs unless DRY_RUN is turned off)
              </p>
            )}
            {prop.args && <Code>{JSON.stringify(prop.args, null, 2)}</Code>}
            {prop.plan && !prop.args && (
              <p className="text-sm text-ink-2">
                Plan confidence {Math.round(prop.plan.confidence * 100)}%. {prop.plan.rationale}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-ink-3">No action attached to this approval.</p>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Relevant memories">
          {ctx.memories.length === 0 ? (
            <p className="text-sm text-ink-3">Nothing relevant was remembered.</p>
          ) : (
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-2">
              {ctx.memories.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Past similar decisions">
          {ctx.similar_decisions.length === 0 ? (
            <p className="text-sm text-ink-3">You have not decided anything like this before.</p>
          ) : (
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-2">
              {ctx.similar_decisions.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Your decision">
          {pending ? (
            <>
              <DecisionPanel
                allowed={ctx.allowed_decisions}
                proposal={(prop?.args ?? prop?.plan ?? null) as Record<string, unknown> | null}
                busy={decide.isPending}
                onDecide={(b) => decide.mutate(b)}
              />
              {decide.isError && (
                <div className="mt-3">
                  <ErrorState error={decide.error} />
                </div>
              )}
            </>
          ) : (
            <div role="status" className="space-y-1 text-sm">
              <p className="font-medium text-ink">
                Decision recorded: {label(String((a.decision as { type?: string } | null)?.type ?? (decide.variables?.type ?? "")))}
              </p>
              <p className="text-ink-2">The run is resuming from its checkpoint.</p>
              <Link to={`/run/${a.task_id}`} className="font-medium text-accent hover:underline">
                Watch it continue →
              </Link>
            </div>
          )}
        </Card>
        <Card title="Ask the agent">
          <ChatPanel approvalId={id} />
        </Card>
      </div>
    </div>
  );
}

export default function ReviewQueue() {
  const { approvalId } = useParams();
  const nav = useNavigate();
  const [tab, setTab] = useState("pending");
  return (
    <>
      <PageHeader title="Review queue" subtitle="Everything the agents will not do without you. Nothing is merged or deployed by the system, ever." />
      <div className="grid gap-5 lg:grid-cols-[20rem_1fr]">
        <div className="space-y-3">
          <div role="tablist" aria-label="Filter" className="inline-flex rounded-lg border border-line bg-surface p-0.5 text-sm">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => {
                  setTab(t.id);
                  if (approvalId) nav("/queue");
                }}
                className={`rounded-md px-3 py-1 font-medium ${tab === t.id ? "bg-accent text-accent-ink" : "text-ink-2 hover:text-ink"}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <List tab={tab} current={approvalId} />
        </div>
        <div className="min-w-0">
          {approvalId ? (
            <Detail id={approvalId} />
          ) : (
            <EmptyState title="Select an approval" hint="Pick one on the left to see the plan, the proposed action and the agent's reasoning." />
          )}
        </div>
      </div>
    </>
  );
}
