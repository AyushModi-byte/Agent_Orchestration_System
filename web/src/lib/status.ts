/** One place that decides how a status looks, so every page agrees. */

export type Tone = "success" | "warning" | "failure" | "escalated" | "info" | "neutral";

export const TONE_CLASSES: Record<Tone, { text: string; bg: string; border: string; dot: string }> = {
  success: { text: "text-success", bg: "bg-success-soft", border: "border-success", dot: "bg-success" },
  warning: { text: "text-warning", bg: "bg-warning-soft", border: "border-warning", dot: "bg-warning" },
  failure: { text: "text-failure", bg: "bg-failure-soft", border: "border-failure", dot: "bg-failure" },
  escalated: { text: "text-escalated", bg: "bg-escalated-soft", border: "border-escalated", dot: "bg-escalated" },
  info: { text: "text-info", bg: "bg-info-soft", border: "border-info", dot: "bg-info" },
  neutral: { text: "text-ink-2", bg: "bg-surface-2", border: "border-line", dot: "bg-ink-3" },
};

export type TraceStatus = "success" | "warning" | "failure" | "escalated";

interface SpanLike {
  name: string;
  status: string;
  attributes?: Record<string, unknown>;
}

/**
 * Trace-node colour. Errors beat everything; a human gate is "escalated"; a reviewer rejection
 * or a tool call that reported failure is a "warning" (the run may have recovered from it).
 */
export function traceStatus(span: SpanLike): TraceStatus {
  const a = span.attributes ?? {};
  if (span.status === "error") return "failure";
  if (span.name.startsWith("hitl.")) return "escalated";
  if (span.name === "reviewer.evaluate" && a.approved === false) return "warning";
  if (a.success === false) return "warning";
  return "success";
}

const TASK_TONES: Record<string, Tone> = {
  queued: "neutral",
  running: "info",
  awaiting_approval: "escalated",
  needs_human: "escalated",
  taken_over: "escalated",
  completed: "success",
  failed: "failure",
};

export function taskTone(status: string): Tone {
  return TASK_TONES[status] ?? "neutral";
}

const STEP_TONES: Record<string, Tone> = {
  pending: "neutral",
  approved: "success",
  rejected: "warning",
  retrying: "warning",
};

export function stepTone(status: string): Tone {
  return STEP_TONES[status] ?? "neutral";
}

export function label(status: string): string {
  return status.replace(/_/g, " ");
}
