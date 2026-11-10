// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { CaseRowSchema, RunDetailSchema, RunSummarySchema } from "../../lib/schema";
import { Results } from "./Results";
import { RunsView } from "./RunsView";
import { RunView } from "./RunView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const run = (fields: Record<string, unknown>) =>
  RunSummarySchema.parse({
    set: "main",
    started_at: 100,
    status: "done",
    pass_marks: { tools: 0.8, reply: 0.7 },
    totals: { passed: 6, cases: 7, tools: 0.89, reply: 0.86, median_seconds: 6, tokens: 61000 },
    ...fields,
  });

it("shows the run playing now with its progress, and stops it", () => {
  const stop = vi.spyOn(api, "stop").mockResolvedValue(run({ run: "r15" }));
  const onOpen = vi.fn();
  render(
    <RunsView
      runs={[
        run({
          run: "r15",
          status: "running",
          progress: {
            done: 2,
            total: 3,
            current: { case: "cart", name: "Adds to the cart once", try: 1, stage: "grading" },
          },
        }),
        run({ run: "r14" }),
      ]}
      label={(id) => `Run ${id.slice(1)}`}
      empty={null}
      onOpen={onOpen}
    />,
  );
  const card = within(screen.getByTestId("live-run"));
  expect(card.getByText("2 of 3 tries · Adds to the cart once · try 1 · grading")).toBeTruthy();
  expect(card.getByRole("img", { name: "2 of 3 tries" })).toBeTruthy();
  fireEvent.click(card.getByRole("button", { name: "Stop" }));
  expect(stop).toHaveBeenCalledWith("r15");
  fireEvent.click(card.getByRole("button", { name: "Open" }));
  expect(onOpen).toHaveBeenCalledWith("r15", null);
});

it("lists benchmark runs as rows of the same table", () => {
  const onBenchmark = vi.fn();
  render(
    <RunsView
      runs={[run({ run: "r14" })]}
      label={(id) => `Run ${id.slice(1)}`}
      empty={null}
      onOpen={vi.fn()}
      benchmarks={[
        run({
          run: "b1",
          set: "bench:longmemeval",
          totals: {
            passed: 19,
            cases: 30,
            tools: null,
            reply: null,
            median_seconds: 41,
            tokens: 412000,
          },
          tests: [{ case: "q1", name: "q1", verdict: "pass", passed: 1, tries: 1 }],
        }),
      ]}
      onBenchmark={onBenchmark}
    />,
  );
  const row = screen.getByRole("button", { name: /19 of 30 right/ });
  expect(within(row).getByText("412k")).toBeTruthy();
  fireEvent.click(row);
  expect(onBenchmark).toHaveBeenCalledWith("b1");
});

it("puts a run button on every result row, always there", () => {
  const onRun = vi.fn();
  const rows = [
    CaseRowSchema.parse({ case: "a", name: "A", verdict: "pass", tries: ["passed"] }),
    CaseRowSchema.parse({ case: "b", name: "B", verdict: "fail", tries: ["failed"] }),
  ];
  render(<Results rows={rows} going={false} onTest={vi.fn()} onRun={onRun} />);
  fireEvent.click(screen.getByRole("button", { name: "Run B" }));
  expect(onRun).toHaveBeenCalledWith(rows[1]);
  expect(screen.getByRole("button", { name: "Run A" }).className).not.toMatch(/opacity-0/);
});

it("puts the scores with their changes in the rail, and no way back of its own", () => {
  vi.spyOn(api, "comparison").mockRejectedValue(new Error("none"));
  render(
    <RunView
      load={{
        status: "ready",
        data: RunDetailSchema.parse({
          ...run({ run: "r14" }),
          cases: [],
          delta: { better: 0, worse: 1, tools: -0.11, reply: -0.14, median_seconds_pct: 0 },
        }),
      }}
      runs={[]}
      label={(id) => `Run ${id.slice(1)}`}
      scopeLabel="All tests"
      going={false}
      onBase={vi.fn()}
      onTest={vi.fn()}
      onRunTest={vi.fn()}
      onRunMore={vi.fn()}
      onStop={async () => {}}
      onAnalyze={async () => {}}
    />,
  );
  const scores = within(screen.getByTestId("run-scores"));
  expect(scores.getByText("0.89")).toBeTruthy();
  expect(scores.getByText("−0.11")).toBeTruthy();
  expect(scores.getByText("±0%")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Runs" })).toBeNull();
});
