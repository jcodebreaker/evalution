// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentMessageSchema } from "../../lib/schema";
import { WriterReply } from "./WriterReply";

afterEach(cleanup);

const MESSAGE = AgentMessageSchema.parse({
  id: "m1",
  role: "agent",
  at: 0,
  text: "Here is the plan",
  plan: {
    id: "p1",
    steps: [{ kind: "message", title: "Ask where the office is" }],
    tries: 3,
    usd: 0.1,
    case: "office",
    weak_lines: [
      { line: "Says the office moved", why: "good_failed", use: { line: "Says Lisbon" } },
      { line: "Gives the code", why: "unsure", use: { contains: ["ZX-42"] } },
      { line: "Is helpful", why: "bad_passed" },
    ],
  },
});

function shown(latest: boolean, onSwapLine = vi.fn()) {
  render(
    <WriterReply
      message={MESSAGE}
      latest={latest}
      runs={[]}
      runLabel={(run) => run}
      testName={(id) => id}
      going={false}
      onReply={vi.fn()}
      onApprove={vi.fn()}
      onSwapLine={onSwapLine}
      onRun={vi.fn()}
      onRunTest={vi.fn()}
      onTest={vi.fn()}
      onLearned={vi.fn()}
    />,
  );
  return onSwapLine;
}

it("flags a weak rubric line with its suggestion, one click from the plan", () => {
  const onSwapLine = shown(true);
  expect(screen.getByText("· Fails a good reply")).toBeTruthy();
  expect(screen.getByText("Says Lisbon")).toBeTruthy();
  expect(screen.getByText("reply.contains “ZX-42”")).toBeTruthy();
  const buttons = screen.getAllByRole("button", { name: "Use this" });
  expect(buttons).toHaveLength(2);
  fireEvent.click(buttons[1] as HTMLElement);
  expect(onSwapLine).toHaveBeenCalledWith("Gives the code");
});

it("offers nothing to click on an older plan", () => {
  shown(false);
  expect(screen.getByText("· Grader unsure")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Use this" })).toBeNull();
});
