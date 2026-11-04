import type { Edge, Node } from "@xyflow/react";

import type { TraceNode } from "../api/client";

export const NODE_W = 244;
export const NODE_H = 124;

export interface NodeSummary {
  /** What the node is, in words. */
  title: string;
  /** What it decided or produced, if anything. */
  decision: string | null;
  tools: string[];
  llmCalls: number;
}

const TITLES: Record<string, string> = {
  "task.run": "Run",
  "task.resume": "Resume after human",
  "memory.retrieve": "Recall memories",
  "memory.extract": "Save lessons",
  "supervisor.plan": "Plan",
  "supervisor.synthesize": "Synthesize report",
  "specialist.run": "Specialist work",
  "reviewer.evaluate": "Review",
  "hitl.escalation": "Escalated to a human",
  "hitl.resolution": "Human decided",
  "tool.call": "Tool call",
  "llm.call": "Model call",
  "replay.run": "Replay",
};

function walk(n: TraceNode, f: (x: TraceNode) => void): void {
  f(n);
  n.children.forEach((c) => walk(c, f));
}

export function summarize(n: TraceNode): NodeSummary {
  const a = n.attributes as Record<string, unknown>;
  const tools: string[] = [];
  let llmCalls = 0;
  n.children.forEach((c) =>
    walk(c, (x) => {
      if (x.name === "tool.call") tools.push(String(x.attributes.tool ?? "tool"));
      if (x.name === "llm.call") llmCalls += 1;
    }),
  );
  let decision: string | null = null;
  if (n.name === "reviewer.evaluate") {
    decision = `${a.approved ? "approved" : "rejected"} · score ${Number(a.score ?? 0).toFixed(2)}`;
  } else if (n.name === "memory.retrieve") {
    decision = `${a.count ?? 0} memories recalled`;
  } else if (n.name === "memory.extract") {
    decision = `${a.items ?? 0} lessons saved`;
  } else if (n.name === "specialist.run") {
    decision = `${a.findings ?? 0} findings · ${a.tool_calls ?? 0} tool calls`;
  } else if (n.name === "hitl.escalation") {
    decision = `${a.kind} gate · ${(a.triggers as string[] | undefined)?.join(", ") ?? ""}`;
  } else if (n.name === "hitl.resolution") {
    decision = `${a.decision}`;
  } else if (n.name === "tool.call") {
    decision = String(a.tool ?? "");
  } else if (n.name === "llm.call") {
    decision = `${n.tokens_in + n.tokens_out} tokens`;
  } else if (n.name === "task.run" || n.name === "task.resume") {
    decision = a.task_status ? String(a.task_status).replace(/_/g, " ") : null;
  }
  const sub = a.subtask_id ? ` ${String(a.subtask_id)}` : "";
  return { title: (TITLES[n.name] ?? n.name) + (n.name === "specialist.run" || n.name === "reviewer.evaluate" ? sub : ""), decision, tools, llmCalls };
}

export interface FlowData extends Record<string, unknown> {
  span: TraceNode;
  summary: NodeSummary;
  hidden: number; // descendants hidden by a collapsed subtree
  expanded: boolean;
}

/** Left-to-right tree: leaves get consecutive rows and parents sit centred on their children. */
export function layoutTrace(roots: TraceNode[], expanded: Set<string>): { nodes: Node<FlowData>[]; edges: Edge[] } {
  const nodes: Node<FlowData>[] = [];
  const edges: Edge[] = [];
  let row = 0;

  const count = (n: TraceNode): number => n.children.reduce((s, c) => s + 1 + count(c), 0);

  const place = (n: TraceNode, depth: number, parent: string | null): number => {
    const open = expanded.has(n.span_id) && n.children.length > 0;
    let y: number;
    if (open) {
      const ys = n.children.map((c) => place(c, depth + 1, n.span_id));
      y = (Math.min(...ys) + Math.max(...ys)) / 2;
    } else {
      y = row * (NODE_H + 24);
      row += 1;
    }
    nodes.push({
      id: n.span_id,
      type: "span",
      position: { x: depth * (NODE_W + 70), y },
      data: { span: n, summary: summarize(n), hidden: open ? 0 : count(n), expanded: open },
    });
    if (parent) edges.push({ id: `${parent}->${n.span_id}`, source: parent, target: n.span_id });
    return y;
  };
  roots.forEach((r) => place(r, 0, null));
  return { nodes, edges };
}

/** Default view: the run, then each of its steps; their tool and model calls stay folded. */
export function defaultExpanded(roots: TraceNode[]): Set<string> {
  return new Set(roots.map((r) => r.span_id));
}

export function allIds(roots: TraceNode[]): Set<string> {
  const s = new Set<string>();
  roots.forEach((r) => walk(r, (x) => s.add(x.span_id)));
  return s;
}
