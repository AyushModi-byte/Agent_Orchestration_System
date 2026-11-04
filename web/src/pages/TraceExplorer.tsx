import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { TraceNode } from "../api/client";
import { useTasks, useTrace } from "../api/hooks";
import TraceDrawer from "../components/TraceDrawer";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader, cx } from "../components/ui";
import { ms, usd } from "../lib/format";
import { TONE_CLASSES, traceStatus, type TraceStatus } from "../lib/status";
import { NODE_H, NODE_W, allIds, defaultExpanded, layoutTrace, type FlowData } from "../lib/trace";

const GLYPH: Record<TraceStatus, string> = { success: "✓", warning: "!", failure: "✕", escalated: "⏸" };
const WORDS: Record<TraceStatus, string> = { success: "Success", warning: "Warning", failure: "Failure", escalated: "Escalated" };

interface Ctx {
  onOpen: (n: TraceNode) => void;
  onToggle: (id: string) => void;
}

function SpanCard({ data }: NodeProps) {
  const { span, summary, hidden, expanded, ctx } = data as FlowData & { ctx: Ctx };
  const status = traceStatus(span);
  const t = TONE_CLASSES[status];
  const hasKids = span.children.length > 0;
  return (
    <div
      style={{ width: NODE_W, minHeight: NODE_H - 8 }}
      className={cx("rounded-xl border-2 bg-surface p-2.5 shadow-card", t.border)}
      data-status={status}
    >
      <Handle type="target" position={Position.Left} className="!bg-ink-3" />
      <div className="flex items-center justify-between gap-2">
        <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.7rem] font-semibold", t.bg, t.text)}>
          <span aria-hidden>{GLYPH[status]}</span>
          {WORDS[status]}
        </span>
        <span className="tabular text-[0.7rem] text-ink-3">{ms(span.duration_ms)}</span>
      </div>
      <button
        type="button"
        onClick={() => ctx.onOpen(span)}
        className="nodrag mt-1.5 block w-full text-left"
        aria-label={`Open details for ${summary.title}${span.agent ? `, ${span.agent}` : ""}`}
      >
        <div className="text-[0.8125rem] font-semibold text-ink">{summary.title}</div>
        <div className="text-[0.75rem] text-ink-2">
          {span.agent && <span className="font-mono">{span.agent}</span>}
          {summary.decision && <span> · {summary.decision}</span>}
        </div>
      </button>
      {summary.tools.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {[...new Set(summary.tools)].map((x) => (
            <span key={x} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.68rem] text-ink-2">
              {x}
            </span>
          ))}
        </div>
      )}
      {span.error && <div className="mt-1.5 line-clamp-2 text-[0.72rem] text-failure">{span.error}</div>}
      <div className="mt-1.5 flex items-center justify-between text-[0.72rem] text-ink-3">
        <span className="tabular">{span.subtree_cost_usd > 0 ? usd(span.subtree_cost_usd) : ""}</span>
        {hasKids && (
          <button
            type="button"
            onClick={() => ctx.onToggle(span.span_id)}
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${summary.title}`}
            className="nodrag rounded px-1.5 py-0.5 font-medium text-accent hover:bg-accent-soft"
          >
            {expanded ? "− collapse" : `+ ${hidden} inside`}
          </button>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-ink-3" />
    </div>
  );
}

const nodeTypes = { span: SpanCard };

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-3 text-[0.75rem] text-ink-2" aria-label="Legend">
      {(Object.keys(WORDS) as TraceStatus[]).map((s) => (
        <li key={s} className="flex items-center gap-1.5">
          <span aria-hidden className={cx("grid size-4 place-items-center rounded-full text-[0.6rem] font-bold", TONE_CLASSES[s].bg, TONE_CLASSES[s].text)}>
            {GLYPH[s]}
          </span>
          {WORDS[s]}
        </li>
      ))}
    </ul>
  );
}

function Tree({ taskId, roots }: { taskId: string; roots: TraceNode[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => defaultExpanded(roots));
  const [open, setOpen] = useState<TraceNode | null>(null);
  useEffect(() => setExpanded(defaultExpanded(roots)), [roots]);

  const onToggle = useCallback(
    (id: string) =>
      setExpanded((s) => {
        const n = new Set(s);
        if (!n.delete(id)) n.add(id);
        return n;
      }),
    [],
  );
  const onOpen = useCallback((n: TraceNode) => setOpen(n), []);
  const { nodes, edges } = useMemo(() => {
    const l = layoutTrace(roots, expanded);
    const ctx: Ctx = { onOpen, onToggle };
    return { nodes: l.nodes.map((n) => ({ ...n, data: { ...n.data, ctx } })), edges: l.edges };
  }, [roots, expanded, onOpen, onToggle]);

  return (
    <>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
        <Legend />
        <div className="flex gap-2">
          <Button onClick={() => setExpanded(allIds(roots))}>Expand all</Button>
          <Button onClick={() => setExpanded(defaultExpanded(roots))}>Collapse</Button>
        </div>
      </div>
      <div className="h-[68vh] min-h-96 overflow-hidden rounded-xl border border-line" role="group" aria-label="Trace tree">
        <ReactFlow
          key={expanded.size}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
          minZoom={0.15}
          nodesDraggable={false}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} color="var(--border)" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable ariaLabel="Trace minimap" nodeColor={(n) => `var(--${traceStatus((n.data as FlowData).span) === "success" ? "success" : traceStatus((n.data as FlowData).span)})`} maskColor="rgb(0 0 0 / 12%)" />
        </ReactFlow>
      </div>
      {open && <TraceDrawer taskId={taskId} node={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function Picker({ current }: { current?: string }) {
  const tasks = useTasks();
  const nav = useNavigate();
  return (
    <label className="flex items-center gap-2 text-sm text-ink-2">
      Run
      <select
        value={current ?? ""}
        onChange={(e) => e.target.value && nav(`/traces/${e.target.value}`)}
        className="max-w-72 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
      >
        <option value="">{tasks.isLoading ? "Loading…" : "Choose a run"}</option>
        {tasks.data?.map((t) => (
          <option key={t.id} value={t.id}>
            {String((t.request as { request?: string }).request ?? t.id)} · {t.id.slice(0, 6)} · {t.status.replace(/_/g, " ")}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function TraceExplorer() {
  const { taskId } = useParams();
  const trace = useTrace(taskId);
  return (
    <>
      <PageHeader
        title="Trace explorer"
        subtitle="Every decision a run made: who did it, with which tools, how long it took and what it cost. Open a node for the full prompt and response."
        actions={<Picker current={taskId} />}
      />
      {!taskId && <EmptyState title="Pick a run to inspect" hint="Choose one above, or start a review from the Run console." action={<Link className="text-sm font-medium text-accent hover:underline" to="/run">Go to Run console</Link>} />}
      {taskId && trace.isLoading && <LoadingState rows={3} label="Loading trace" />}
      {taskId && trace.isError && (
        <ErrorState error={trace.error} retry={() => trace.refetch()} />
      )}
      {trace.data && (
        <div className="space-y-4">
          <Card>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <Badge tone={trace.data.status === "ok" ? "success" : "failure"}>{trace.data.status === "ok" ? "No errors" : "Has errors"}</Badge>
              <span className="text-ink-2">
                <span className="tabular font-medium text-ink">{trace.data.span_count}</span> spans
              </span>
              <span className="text-ink-2">
                Total cost <span className="tabular font-medium text-ink">{usd(trace.data.total_cost_usd)}</span>
              </span>
              <Link className="ml-auto font-medium text-accent hover:underline" to={`/run/${trace.data.task_id}`}>
                Back to run →
              </Link>
            </div>
          </Card>
          <Tree taskId={trace.data.task_id} roots={trace.data.roots} />
        </div>
      )}
    </>
  );
}
