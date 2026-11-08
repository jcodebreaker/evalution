// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { isolatedWords } from "../../lib/isolation";
import {
  AgentMessageSchema,
  CompareSchema,
  type Isolation,
  IsolationSchema,
  RunSummarySchema,
} from "../../lib/schema";
import { PlanDetails } from "../environment/PlanDetails";
import { WriterReply } from "../writer/WriterReply";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const PLAN = {
  stores: [
    {
      id: "postgres-app",
      kind: "postgres",
      locator: "eval-bot-postgres-1:app",
      strategy: "relocate",
      moved_by: ["DATABASE_URL"],
      seen: true,
    },
    {
      id: "volume-artifact-data",
      kind: "volume",
      locator: "volume:artifact-data",
      strategy: "relocate",
      seen: true,
    },
  ],
  exclude: ["app.phoenix.spans"],
  report: "Rehearsed on eval-bot in 9s:\nProven.",
  leaks: [],
};

const view = (extra: Record<string, unknown>): Isolation =>
  IsolationSchema.parse({ target: "bot", can_run: true, ...extra });

function shows(found: Isolation, onChange = vi.fn()) {
  render(<PlanDetails isolation={found} onChange={onChange} />);
  return onChange;
}

it("shows a proven plan in plain words: what moves each store, what's left alone, and undo", async () => {
  const onChange = shows(view({ status: "proven", version: 3, plan: PLAN, can_undo: true }));
  expect(screen.getByText(/^Proven \(plan v3\)/)).toBeTruthy();
  expect(screen.getByText("Postgres database")).toBeTruthy();
  expect(
    screen.getByText("Starts from a fresh copy each try, pointed there by DATABASE_URL"),
  ).toBeTruthy();
  expect(screen.getByText("Docker volume")).toBeTruthy();
  expect(screen.getByText(/Left alone.*app\.phoenix\.spans/)).toBeTruthy();
  const undone = view({ status: "none" });
  const undo = vi.spyOn(api, "undoIsolation").mockResolvedValue(undone);
  fireEvent.click(screen.getByRole("button", { name: "Undo the last plan" }));
  await waitFor(() => expect(undo).toHaveBeenCalledWith("bot"));
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(undone));
});

it("names where the last check found what a failed plan misses", () => {
  shows(
    view({
      status: "failed",
      version: 1,
      plan: { ...PLAN, leaks: ["eval-bot-app-1:/data/memory.idx"] },
    }),
  );
  expect(screen.getByText(/^Not proven/)).toBeTruthy();
  expect(screen.getByText("Where the last check found what the plan misses")).toBeTruthy();
  expect(screen.getByText("eval-bot-app-1:/data/memory.idx")).toBeTruthy();
});

it("says which commit files left a plan out of date", () => {
  shows(view({ status: "stale", version: 2, plan: PLAN, stale_files: ["db/utils.py"] }));
  expect(screen.getByText("Out of date: a commit changed db/utils.py.")).toBeTruthy();
});

it("labels runs and tries by how they were isolated", () => {
  expect(isolatedWords({ how: "proven", plan_version: 3 })).toBe("Isolated (proven plan v3)");
  expect(isolatedWords({ how: "identity", plan_version: null })).toBe("Identity only");
  expect(isolatedWords({ how: "none", plan_version: null })).toBe("Not isolated");

  // Older payloads carry no isolation and still parse.
  const base = {
    run: "r1",
    set: "s",
    started_at: 0,
    status: "done",
    pass_marks: { tools: 1, reply: 1 },
    totals: { passed: 0, cases: 0, tools: null, reply: null, median_seconds: 0, tokens: 0 },
  };
  expect(RunSummarySchema.safeParse(base).success).toBe(true);
  const run = RunSummarySchema.parse({ ...base, isolation: { how: "proven", plan_version: 2 } });
  expect(run.isolation?.how).toBe("proven");

  // The try's field, as the test view reads it.
  const isolation = CompareSchema.shape.isolation.parse({
    how: "proven",
    plan_version: 3,
    restore_seconds: 4,
    checked: ["a", "b"],
    problem: "The earlier try's code word was still in memory.idx",
  });
  expect(CompareSchema.shape.isolation.parse(undefined)).toBeUndefined();
  expect(isolation?.stores).toBeUndefined();
});

const PROPOSAL = AgentMessageSchema.parse({
  id: "m1",
  role: "agent",
  at: 0,
  text: "I'd change the isolation plan",
  isolation: {
    id: "iso-1",
    version: 1,
    status: "draft",
    from: "the saved plan",
    changes: ["Add a Docker volume artifact-data, started fresh each try", "Leave alone app.spans"],
  },
});

function proposal(latest: boolean, onApprove = vi.fn()) {
  render(
    <WriterReply
      message={PROPOSAL}
      latest={latest}
      runs={[]}
      runLabel={(run) => run}
      testName={(id) => id}
      going={false}
      onReply={vi.fn()}
      onApprove={onApprove}
      onSwapLine={vi.fn()}
      onRun={vi.fn()}
      onRunTest={vi.fn()}
      onTest={vi.fn()}
      onLearned={vi.fn()}
    />,
  );
  return onApprove;
}

it("renders the writer's isolation proposal as a list, approved with the usual button", () => {
  const onApprove = proposal(true);
  expect(screen.getByText("Changes to the isolation plan, from the saved plan")).toBeTruthy();
  expect(
    screen.getByText("Add a Docker volume artifact-data, started fresh each try"),
  ).toBeTruthy();
  expect(screen.getByText("Leave alone app.spans")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Approve and rehearse" }));
  expect(onApprove).toHaveBeenCalledWith("iso-1");
});

it("offers no approval on an older proposal", () => {
  proposal(false);
  expect(screen.queryByRole("button", { name: "Approve and rehearse" })).toBeNull();
});
