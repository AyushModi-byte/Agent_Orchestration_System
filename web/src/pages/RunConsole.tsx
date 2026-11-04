import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { isTerminal, useApprovals, useCreateTask, useTask, useTaskEvents, useTasks } from "../api/hooks";
import type { TaskEventMsg } from "../api/types";
import PlanGraph from "../components/PlanGraph";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, Mono, PageHeader, StatusBadge } from "../components/ui";
import { ago } from "../lib/format";
import { getUserId, setUserId } from "../lib/user";
import { taskTone } from "../lib/status";

function describe(e: TaskEventMsg): string {
  const s = (k: string) => String(e[k] ?? "");
  switch (e.type) {
    case "intake": return "Request received";
    case "plan": return `Plan ready: ${s("steps")} subtasks`;
    case "plan_failed": return `Plan rejected: ${s("error")}`;
    case "subtask_done": return `${s("subtask")} finished`;
    case "subtask_failed": return `${s("subtask")} failed, retrying`;
    case "review": return `Reviewer ${e.approved ? "approved" : "rejected"} ${s("subtask")}`;
    case "synthesized": return "Findings synthesized into a report";
    case "delivered": return "Report delivered";
    case "decision": return `Human decision at the ${s("gate")} gate: ${s("decision")}`;
    case "approval_requested": return `Waiting for a human (${s("kind")}, ${s("level")})`;
    case "action_done": return `Action done: ${s("tool")}${(e.result as { dry_run?: boolean } | undefined)?.dry_run ? " (dry run)" : ""}`;
    case "action_failed": return `Action failed: ${s("tool")}`;
    case "memory_saved": return `Saved ${s("items")} lessons to memory`;
    case "notified": return "Plan passed policy checks";
    case "failed": return `Run failed: ${s("error")}`;
    default: return e.type.replace(/_/g, " ");
  }
}

function toneOf(e: TaskEventMsg): "success" | "warning" | "failure" | "escalated" | "neutral" {
  if (e.type === "failed" || e.type === "action_failed") return "failure";
  if (e.type === "approval_requested" || e.type === "decision") return "escalated";
  if (e.type === "subtask_failed" || e.type === "plan_failed" || (e.type === "review" && e.approved === false)) return "warning";
  if (e.type === "subtask_done" || e.type === "delivered" || (e.type === "review" && e.approved)) return "success";
  return "neutral";
}

function StartForm({ onStarted }: { onStarted: (id: string) => void }) {
  const create = useCreateTask();
  const [repo, setRepo] = useState("acme/shop");
  const [pr, setPr] = useState("2");
  const [head, setHead] = useState("fix/sql-injection");
  const [user, setUser] = useState(getUserId());
  const [planApproval, setPlanApproval] = useState(false);

  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    setUserId(user);
    create.mutate(
      {
        request: `Review PR #${pr} on ${repo}`,
        repo,
        pr_number: Number(pr),
        head_ref: head,
        user_id: user,
        review_required: planApproval,
      },
      { onSuccess: (r) => onStarted(r.id) },
    );
  };
  const field = "mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3";
  return (
    <Card title="Start a review">
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm font-medium text-ink-2">
          Repository
          <input required value={repo} onChange={(e) => setRepo(e.target.value)} pattern="[^/\s]+/[^/\s]+" title="owner/name" className={field} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm font-medium text-ink-2">
            PR number
            <input required type="number" min={1} value={pr} onChange={(e) => setPr(e.target.value)} className={field} />
          </label>
          <label className="block text-sm font-medium text-ink-2">
            Acting as
            <input required value={user} onChange={(e) => setUser(e.target.value)} className={field} />
          </label>
        </div>
        <label className="block text-sm font-medium text-ink-2">
          Head branch <span className="font-normal text-ink-3">(fixes are pushed here after approval)</span>
          <input required value={head} onChange={(e) => setHead(e.target.value)} className={field} />
        </label>
        <label className="flex items-start gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={planApproval} onChange={(e) => setPlanApproval(e.target.checked)} className="mt-1 accent-[var(--accent)]" />
          Ask me to approve the plan before any work starts
        </label>
        <Button type="submit" variant="primary" className="w-full" disabled={create.isPending}>
          {create.isPending ? "Starting…" : "Start review"}
        </Button>
        {create.isError && <ErrorState error={create.error} />}
      </form>
    </Card>
  );
}

function RunView({ taskId }: { taskId: string }) {
  const task = useTask(taskId);
  const { events } = useTaskEvents(taskId);
  const pending = useApprovals("pending");
  const waiting = pending.data?.find((a) => a.task_id === taskId);

  if (task.isLoading) return <LoadingState rows={4} label="Loading run" />;
  if (task.isError) return <ErrorState error={task.error} retry={() => task.refetch()} />;
  const t = task.data!;
  const live = !isTerminal(t.status);
  const report = t.report as { summary?: string; findings?: { file: string; line: number; rule: string; severity: string; evidence: string }[]; fixes?: { path?: string; message?: string }[] } | null;

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={t.status} tone={taskTone(t.status)} />
          {live && <span aria-hidden className="live-dot size-2 rounded-full bg-info" />}
          <span className="text-sm font-medium text-ink">{String((t.request as { request?: string }).request ?? "")}</span>
          <Mono className="ml-auto text-ink-3">{t.id.slice(0, 8)}</Mono>
          <Link className="text-sm font-medium text-accent hover:underline" to={`/traces/${t.id}`}>
            Open trace →
          </Link>
        </div>
        {waiting && (
          <div role="alert" className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-escalated-soft px-3 py-2.5 text-sm text-escalated">
            <span>This run is paused: a human must decide ({waiting.kind}).</span>
            <Link to={`/queue/${waiting.id}`} className="rounded-md bg-escalated px-3 py-1.5 font-medium text-white dark:text-black">
              Review now
            </Link>
          </div>
        )}
        {t.error && <p className="mt-3 text-sm text-failure">{t.error}</p>}
      </Card>

      <Card title="Plan" aside={<span className="text-[0.75rem] text-ink-3">{t.steps.length} subtasks</span>}>
        {t.steps.length === 0 ? (
          <EmptyState title="Waiting for the supervisor" hint="The plan appears here as soon as the supervisor has decomposed the request." />
        ) : (
          <PlanGraph steps={t.steps} />
        )}
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Live progress">
          {events.length === 0 ? (
            <p className="text-sm text-ink-3">{live ? "Connecting to the event stream…" : "No events were recorded."}</p>
          ) : (
            <ol className="space-y-2" aria-live="polite">
              {events.map((e, i) => (
                <li key={i} className="flex items-start gap-2.5 text-sm">
                  <Badge tone={toneOf(e)} dot>
                    {e.type.replace(/_/g, " ")}
                  </Badge>
                  <span className="pt-0.5 text-ink-2">{describe(e)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>

        <Card title="Result">
          {!report ? (
            <p className="text-sm text-ink-3">{live ? "The report appears when the run finishes." : "This run produced no report."}</p>
          ) : (
            <div className="space-y-3 text-sm">
              <p className="text-ink">{report.summary}</p>
              {report.findings && report.findings.length > 0 && (
                <ul className="divide-y divide-line rounded-lg border border-line">
                  {report.findings.map((f, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                      <Badge tone={f.severity === "high" ? "failure" : f.severity === "medium" ? "warning" : "neutral"}>{f.severity}</Badge>
                      <Mono>{f.file}:{f.line}</Mono>
                      <span className="text-ink-2">{f.rule}</span>
                    </li>
                  ))}
                </ul>
              )}
              {report.fixes && report.fixes.length > 0 && (
                <p className="text-ink-2">
                  Proposed fix: <Mono>{report.fixes[0].path}</Mono> — {report.fixes[0].message}
                </p>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function Recent({ current }: { current?: string }) {
  const tasks = useTasks();
  if (tasks.isLoading) return <LoadingState rows={2} label="Loading recent runs" />;
  if (tasks.isError) return <ErrorState error={tasks.error} retry={() => tasks.refetch()} />;
  if (!tasks.data?.length) return <p className="text-sm text-ink-3">No runs yet.</p>;
  return (
    <ul className="space-y-1.5">
      {tasks.data.map((t) => (
        <li key={t.id}>
          <Link
            to={`/run/${t.id}`}
            aria-current={t.id === current ? "page" : undefined}
            className={`flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm hover:bg-surface-2 ${t.id === current ? "bg-accent-soft" : ""}`}
          >
            <span className="truncate text-ink">{String((t.request as { request?: string }).request ?? t.id)}</span>
            <span className="flex shrink-0 items-center gap-2">
              <span className="text-[0.75rem] text-ink-3">{ago(t.created_at)}</span>
              <StatusBadge status={t.status} tone={taskTone(t.status)} />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default function RunConsole() {
  const { taskId } = useParams();
  const nav = useNavigate();
  return (
    <>
      <PageHeader title="Run console" subtitle="Start a pull-request review and watch the supervisor, specialists and reviewer work through it." />
      <div className="grid gap-5 lg:grid-cols-[22rem_1fr]">
        <div className="space-y-5">
          <StartForm onStarted={(id) => nav(`/run/${id}`)} />
          <Card title="Recent runs">
            <Recent current={taskId} />
          </Card>
        </div>
        <div className="min-w-0">
          {taskId ? (
            <RunView taskId={taskId} />
          ) : (
            <EmptyState title="No run selected" hint="Start a review on the left, or pick a recent run, to see its plan and live progress." />
          )}
        </div>
      </div>
    </>
  );
}
