import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { TONE_CLASSES, label, stepTone } from "../lib/status";
import { cx } from "./ui";

export interface PlanStepView {
  id: string;
  description: string;
  specialist: string;
  depends_on: string[];
  status: string;
}

const W = 232;
const H = 84;

/** Longest-path layering: a step sits one column right of its deepest dependency. */
export function layout(steps: PlanStepView[]): { nodes: Node[]; edges: Edge[] } {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  const level = (id: string, seen: Set<string> = new Set()): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0; // a cycle cannot happen in a validated plan; do not loop if it does
    seen.add(id);
    const deps = (byId.get(id)?.depends_on ?? []).filter((d) => byId.has(d));
    const d = deps.length ? 1 + Math.max(...deps.map((x) => level(x, seen))) : 0;
    depth.set(id, d);
    return d;
  };
  const rows = new Map<number, number>();
  const nodes: Node[] = steps.map((s) => {
    const col = level(s.id);
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    return { id: s.id, type: "step", position: { x: col * (W + 64), y: row * (H + 28) }, data: { step: s } };
  });
  const edges: Edge[] = steps.flatMap((s) =>
    s.depends_on
      .filter((d) => byId.has(d))
      .map((d) => ({
        id: `${d}->${s.id}`,
        source: d,
        target: s.id,
        markerEnd: { type: MarkerType.ArrowClosed },
        animated: byId.get(d)?.status === "approved" && s.status === "pending",
      })),
  );
  return { nodes, edges };
}

function StepNode({ data }: NodeProps) {
  const step = (data as { step: PlanStepView }).step;
  const t = TONE_CLASSES[stepTone(step.status)];
  return (
    <div
      style={{ width: W, minHeight: H }}
      className={cx("rounded-xl border-2 bg-surface px-3 py-2 shadow-card", t.border)}
      aria-label={`${step.id} ${step.specialist}, ${label(step.status)}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-ink-3" />
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[0.75rem] text-ink-3">{step.id}</span>
        <span className={cx("rounded-full px-2 py-0.5 text-[0.7rem] font-medium", t.bg, t.text)}>{label(step.status)}</span>
      </div>
      <div className="mt-1 text-[0.8125rem] font-semibold text-ink">{label(step.specialist)}</div>
      <div className="line-clamp-2 text-[0.75rem] leading-snug text-ink-2">{step.description}</div>
      <Handle type="source" position={Position.Right} className="!bg-ink-3" />
    </div>
  );
}

const nodeTypes = { step: StepNode };

export default function PlanGraph({ steps }: { steps: PlanStepView[] }) {
  const { nodes, edges } = useMemo(() => layout(steps), [steps]);
  const perColumn = new Map<number, number>();
  nodes.forEach((n) => perColumn.set(n.position.x, (perColumn.get(n.position.x) ?? 0) + 1));
  const rowsMax = Math.max(1, ...perColumn.values());
  return (
    <div
      className="w-full overflow-hidden rounded-xl border border-line"
      style={{ height: Math.max(260, rowsMax * (H + 28) + 70) }}
      role="group"
      aria-label="Plan graph"
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
        minZoom={0.4}
      >
        <Background gap={20} color="var(--border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
