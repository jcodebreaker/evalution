// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { type AgentMessage, AgentMessageSchema } from "../../lib/schema";
import { WriterReply } from "./WriterReply";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const MESSAGE = AgentMessageSchema.parse({
  id: "m1",
  role: "agent",
  at: 0,
  text: "Here is the plan",
  plan: {
    id: "p1",
    steps: [{ kind: "message", title: "Ask where the office is" }],
    tries: 3,
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

const pagePlan = (offer: Record<string, unknown> | null) =>
  AgentMessageSchema.parse({
    id: "m2",
    role: "agent",
    at: 0,
    text: "Here is the plan",
    plan: {
      id: "p2",
      steps: [{ kind: "message", title: "Say thanks" }],
      tries: 3,
      case: "thanks",
      page: {
        shows: [{ role: "button", name: "Thumbs up", within: "reply", turn: 2 }],
        hides: [{ text: "Error", within: "page" }],
        looks: [{ question: "Is there a thumbs-up icon under the reply?" }],
      },
    },
    ...(offer ? { page_offer: offer } : {}),
  });

function shownMessage(message: AgentMessage) {
  render(
    <WriterReply
      message={message}
      latest
      runs={[]}
      runLabel={(run) => run}
      testName={(id) => id}
      going={false}
      onReply={vi.fn()}
      onApprove={vi.fn()}
      onSwapLine={vi.fn()}
      onRun={vi.fn()}
      onRunTest={vi.fn()}
      onTest={vi.fn()}
      onLearned={vi.fn()}
    />,
  );
}

it("says a plan's UI checks as sentences, each with its turn", () => {
  shownMessage(pagePlan(null));
  expect(
    screen.getAllByTestId("ui-check").map((line) => line.getAttribute("data-sentence")),
  ).toEqual([
    "Shows button 'Thumbs up' under the reply · turn 2",
    "Doesn't show text 'Error' on the page · every reply",
    "Looks: Is there a thumbs-up icon under the reply? · every reply",
  ]);
  expect(screen.queryByTestId("plan-page-offer")).toBeNull();
});

it("offers to learn the page under a plan with UI checks when the app has no page map", async () => {
  const learn = vi.spyOn(api, "learnPage").mockResolvedValue(null as never);
  shownMessage(
    pagePlan({
      target: "chat",
      offer: true,
      underway: false,
      found_in: "app/chat.tsx",
      dismissed: false,
      can_learn: true,
      why_not: null,
    }),
  );
  expect(screen.getByRole("button", { name: /Add to tests/ })).toBeTruthy();
  const offer = screen.getByTestId("plan-page-offer");
  expect(offer.textContent).toContain("need chat's page learned first");
  fireEvent.click(within(offer).getByRole("button", { name: "Set up UI testing" }));
  await waitFor(() => expect(learn).toHaveBeenCalledWith("chat"));
});
