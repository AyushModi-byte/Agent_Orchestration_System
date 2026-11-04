/** Narrower shapes for the places where the API returns free-form JSON (approval context). */

export type DecisionType = "approve" | "modify" | "reject" | "take_over";

export interface DecisionBody {
  type: DecisionType;
  reason?: string;
  edited?: Record<string, unknown>;
  output?: Record<string, unknown>;
}

export interface Trigger {
  name: string;
  level: string;
  reason: string;
  subtask_id?: string | null;
}

export interface PlanStepCtx {
  id: string;
  description: string;
  specialist: string;
  depends_on: string[];
}

export interface ProposedAction {
  summary?: string;
  reasoning?: string;
  tool?: string;
  agent?: string;
  args?: Record<string, unknown>;
  plan?: { steps: PlanStepCtx[]; confidence: number; rationale: string };
}

export interface ApprovalContext {
  task_id: string;
  kind: string;
  level: string;
  allowed_decisions: DecisionType[];
  triggers: Trigger[];
  task: { request?: string; repo?: string; pr_number?: number } | null;
  plan: { steps: PlanStepCtx[]; confidence: number; rationale: string } | null;
  plan_error: string | null;
  completed_steps: { id: string; specialist: string; description: string }[];
  current_step: PlanStepCtx | null;
  proposed_action: ProposedAction | null;
  memories: string[];
  similar_decisions: string[];
}

export function asContext(raw: unknown): ApprovalContext {
  return raw as ApprovalContext;
}

export interface TaskEventMsg {
  type: string;
  [key: string]: unknown;
}
