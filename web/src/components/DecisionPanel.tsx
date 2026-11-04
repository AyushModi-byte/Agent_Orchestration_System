import { useState } from "react";

import type { DecisionBody, DecisionType } from "../api/types";
import { Button, cx } from "./ui";

interface Props {
  /** Decisions the policy allows at this gate; anything else is not offered at all. */
  allowed: DecisionType[];
  /** Pre-fills the "modify" editor with what the agent proposed. */
  proposal?: Record<string, unknown> | null;
  busy?: boolean;
  disabled?: boolean;
  onDecide: (body: DecisionBody) => void;
}

const META: Record<DecisionType, { label: string; hint: string }> = {
  approve: { label: "Approve", hint: "Run it as proposed." },
  modify: { label: "Modify", hint: "Edit the proposal as JSON, then submit your version." },
  reject: { label: "Reject", hint: "Say why. The supervisor re-plans using your reason." },
  take_over: { label: "Take over", hint: "Stop the agents and write the outcome yourself." },
};
const ORDER: DecisionType[] = ["approve", "modify", "reject", "take_over"];

/** Validates and builds the request body for the three decisions that need input. */
export function buildDecision(
  mode: Exclude<DecisionType, "approve">,
  text: string,
): { body: DecisionBody } | { error: string } {
  const value = text.trim();
  if (mode === "reject") {
    return value ? { body: { type: "reject", reason: value } } : { error: "A reason is required." };
  }
  if (mode === "take_over") {
    return value ? { body: { type: "take_over", output: { summary: value } } } : { error: "Describe the outcome you are providing." };
  }
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { error: "The edit must be a JSON object." };
    }
    return { body: { type: "modify", edited: parsed as Record<string, unknown> } };
  } catch (e) {
    return { error: `Not valid JSON: ${(e as Error).message}` };
  }
}

export default function DecisionPanel({ allowed, proposal, busy, disabled, onDecide }: Props) {
  const [mode, setMode] = useState<Exclude<DecisionType, "approve"> | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const off = busy || disabled;

  const choose = (d: DecisionType) => {
    setError(null);
    if (d === "approve") {
      onDecide({ type: "approve" });
      return;
    }
    const next = mode === d ? null : d;
    setMode(next);
    setText(d === "modify" && proposal ? JSON.stringify(proposal, null, 2) : "");
  };

  const submit = () => {
    if (!mode) return;
    const r = buildDecision(mode, text);
    if ("error" in r) setError(r.error);
    else {
      setError(null);
      onDecide(r.body);
    }
  };

  const labelId = "decision-field";
  return (
    <div className="space-y-3">
      <div role="group" aria-label="Decision" className="flex flex-wrap gap-2">
        {ORDER.filter((d) => allowed.includes(d)).map((d) => (
          <Button
            key={d}
            variant={d === "approve" ? "primary" : d === "reject" ? "danger" : "secondary"}
            aria-pressed={d === "approve" ? undefined : mode === d}
            disabled={off}
            onClick={() => choose(d)}
            className={cx(mode === d && "ring-2 ring-accent")}
          >
            {META[d].label}
          </Button>
        ))}
      </div>
      {mode && (
        <div className="space-y-2 rounded-lg border border-line bg-surface-2 p-3">
          <label htmlFor={labelId} className="block text-sm font-medium text-ink">
            {META[mode].label}
            <span className="ml-2 font-normal text-ink-2">{META[mode].hint}</span>
          </label>
          <textarea
            id={labelId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={mode === "modify" ? 10 : 3}
            spellCheck={mode !== "modify"}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${labelId}-error` : undefined}
            className={cx("w-full rounded-lg border border-line bg-surface p-2.5 text-sm text-ink", mode === "modify" && "font-mono text-[0.8125rem]")}
          />
          {error && (
            <p id={`${labelId}-error`} role="alert" className="text-sm text-failure">
              {error}
            </p>
          )}
          <Button variant="primary" onClick={submit} disabled={off}>
            {busy ? "Submitting…" : `Submit ${META[mode].label.toLowerCase()}`}
          </Button>
        </div>
      )}
    </div>
  );
}
