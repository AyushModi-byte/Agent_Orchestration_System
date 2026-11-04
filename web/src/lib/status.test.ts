import { describe, expect, it } from "vitest";

import { stepTone, taskTone, traceStatus } from "./status";

const span = (name: string, status = "ok", attributes: Record<string, unknown> = {}) => ({ name, status, attributes });

describe("traceStatus", () => {
  it("is success for an ordinary span", () => {
    expect(traceStatus(span("specialist.run"))).toBe("success");
  });
  it("is failure for an errored span, even a human gate", () => {
    expect(traceStatus(span("tool.call", "error"))).toBe("failure");
    expect(traceStatus(span("hitl.escalation", "error"))).toBe("failure");
  });
  it("marks human gates as escalated", () => {
    expect(traceStatus(span("hitl.escalation"))).toBe("escalated");
    expect(traceStatus(span("hitl.resolution"))).toBe("escalated");
  });
  it("warns on a reviewer rejection but not on an approval", () => {
    expect(traceStatus(span("reviewer.evaluate", "ok", { approved: false }))).toBe("warning");
    expect(traceStatus(span("reviewer.evaluate", "ok", { approved: true }))).toBe("success");
  });
  it("warns when a tool reports it did not succeed", () => {
    expect(traceStatus(span("tool.call", "ok", { success: false }))).toBe("warning");
  });
});

describe("task and step tones", () => {
  it("maps run states", () => {
    expect(taskTone("completed")).toBe("success");
    expect(taskTone("failed")).toBe("failure");
    expect(taskTone("running")).toBe("info");
    expect(taskTone("awaiting_approval")).toBe("escalated");
    expect(taskTone("taken_over")).toBe("escalated");
    expect(taskTone("something-new")).toBe("neutral");
  });
  it("maps plan step states", () => {
    expect(stepTone("approved")).toBe("success");
    expect(stepTone("rejected")).toBe("warning");
    expect(stepTone("retrying")).toBe("warning");
    expect(stepTone("pending")).toBe("neutral");
  });
});
