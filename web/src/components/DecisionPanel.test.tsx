import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import DecisionPanel, { buildDecision } from "./DecisionPanel";

const ALL = ["approve", "modify", "reject", "take_over"] as const;

function setup(props: Partial<React.ComponentProps<typeof DecisionPanel>> = {}) {
  const onDecide = vi.fn();
  render(<DecisionPanel allowed={[...ALL]} onDecide={onDecide} {...props} />);
  return { onDecide, user: userEvent.setup() };
}

describe("DecisionPanel", () => {
  it("only offers the decisions the policy allows", () => {
    setup({ allowed: ["approve", "reject"] });
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Modify" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take over" })).not.toBeInTheDocument();
  });

  it("approves in one click", async () => {
    const { onDecide, user } = setup();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(onDecide).toHaveBeenCalledWith({ type: "approve" });
  });

  it("will not reject without a reason, then sends it", async () => {
    const { onDecide, user } = setup();
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Submit reject" }));
    expect(screen.getByRole("alert")).toHaveTextContent("A reason is required");
    expect(onDecide).not.toHaveBeenCalled();

    await user.type(screen.getByRole("textbox"), "  use parameterised queries ");
    await user.click(screen.getByRole("button", { name: "Submit reject" }));
    expect(onDecide).toHaveBeenCalledWith({ type: "reject", reason: "use parameterised queries" });
  });

  it("modify starts from the proposal and refuses invalid JSON", async () => {
    const { onDecide, user } = setup({ proposal: { path: "app/db.py" } });
    await user.click(screen.getByRole("button", { name: "Modify" }));
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(JSON.parse(box.value)).toEqual({ path: "app/db.py" });

    await user.clear(box);
    await user.type(box, "not json");
    await user.click(screen.getByRole("button", { name: "Submit modify" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Not valid JSON");
    expect(onDecide).not.toHaveBeenCalled();
  });

  it("take over needs an outcome and sends it as output", async () => {
    const { onDecide, user } = setup();
    await user.click(screen.getByRole("button", { name: "Take over" }));
    await user.type(screen.getByRole("textbox"), "I will fix this by hand");
    await user.click(screen.getByRole("button", { name: "Submit take over" }));
    expect(onDecide).toHaveBeenCalledWith({ type: "take_over", output: { summary: "I will fix this by hand" } });
  });

  it("disables every control while a decision is being sent", () => {
    setup({ busy: true });
    for (const b of screen.getAllByRole("button")) expect(b).toBeDisabled();
  });
});

describe("buildDecision", () => {
  it("rejects non-object edits", () => {
    expect(buildDecision("modify", "[1]")).toEqual({ error: "The edit must be a JSON object." });
    expect(buildDecision("modify", '{"a":1}')).toEqual({ body: { type: "modify", edited: { a: 1 } } });
  });
});
