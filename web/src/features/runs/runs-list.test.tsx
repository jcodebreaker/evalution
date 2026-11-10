// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { CaseRowSchema, EstimateSchema, RunDetailSchema, RunSummarySchema } from "../../lib/schema";
import { Results } from "./Results";
import { RunConfirm } from "./RunConfirm";
import { RunsView } from "./RunsView";
import { RunView } from "./RunView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

it("lists a queued run, after the run that plays, and stops it off the queue", () => {
  const stop = vi.spyOn(api, "stop").mockResolvedValue(run({ run: "r16" }));
  const queued = run({ run: "r16", status: "queued", started_at: 200, queued_at: 200 });
  expect(queued.status).toBe("queued");
  expect(queued.queued_at).toBe(200);
  render(
    <RunsView
      runs={[queued, run({ run: "r15", status: "running", started_at: 150 }), run({ run: "r14" })]}
      label={(id) => `Run ${id.slice(1)}`}
      empty={null}
      onOpen={vi.fn()}
    />,
  );
  const card = within(screen.getByRole("region", { name: "Run 16, queued" }));
  expect(card.getByText("Queued · after Run 15")).toBeTruthy();
  fireEvent.click(card.getByRole("button", { name: "Stop" }));
  expect(stop).toHaveBeenCalledWith("r16");
  const row = within(screen.getByRole("button", { name: /^Run 16/ }));
  expect(row.getByText("Queued · after Run 15")).toBeTruthy();
});

it("starts a run while another plays as queued, not as an error, and shows it queued", async () => {
  const answer = {
    run: "r16",
    target: "relay-lite",
    set: "main",
    started_at: 200,
    queued_at: 200,
    status: "queued",
    pass_marks: { tools: 0.8, reply: 0.7 },
    totals: { passed: 0, cases: 1, tools: null, reply: null, median_seconds: 0, tokens: 0 },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      path.startsWith("/v1/evals/runs") && !path.includes("?")
        ? new Response(JSON.stringify(answer), {
            status: 202,
            headers: { "content-type": "application/json" },
          })
        : new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
    ),
  );
  const onStarted = vi.fn();
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 1,
    tries: 1,
    chats: 1,
    plays: [{ how: "api", cases: ["a"], seconds: 5, text: "" }],
  });
  render(
    <RunConfirm
      target={{
        scope: { target: "relay-lite", set: "main" },
        test: { id: "a", name: "A" },
        estimate,
      }}
      onClose={vi.fn()}
      onStarted={onStarted}
    />,
  );
  const go = await screen.findByRole("button", { name: "Run 1 test" });
  await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(go);
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  expect(onStarted.mock.calls[0]?.[0].status).toBe("queued");
  expect(screen.queryByText(/Couldn't start/)).toBeNull();
  cleanup();
  render(
    <RunView
      load={{
        status: "ready",
        data: RunDetailSchema.parse({ ...answer, cases: [] }),
      }}
      runs={[
        RunSummarySchema.parse(answer),
        run({ run: "r15", status: "running", started_at: 150 }),
      ]}
      label={(id) => `Run ${id.slice(1)}`}
      scopeLabel="All tests"
      going={true}
      onBase={vi.fn()}
      onTest={vi.fn()}
      onRunTest={vi.fn()}
      onRunMore={vi.fn()}
      onStop={async () => {}}
      onAnalyze={async () => {}}
    />,
  );
  expect(screen.getByText("Queued")).toBeTruthy();
  expect(screen.getByTestId("queued-run").textContent).toMatch(/^Queued · after Run 15/);
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
});

it("reads no runs or tests again when the screen holds the set's already", async () => {
  const runs = vi.spyOn(api, "runs");
  const cases = vi.spyOn(api, "cases");
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 2,
    tries: 2,
    chats: 2,
    plays: [{ how: "api", cases: ["a", "b"], seconds: 5, text: "" }],
  });
  render(
    <RunConfirm
      target={{ scope: { target: "relay-lite", set: "main" }, estimate }}
      known={{
        runs: [
          run({ run: "r1", started_at: 1 }),
          run({
            run: "r2",
            started_at: 2,
            totals: {
              passed: 1,
              cases: 2,
              tools: null,
              reply: null,
              median_seconds: 6,
              tokens: 9000,
            },
          }),
        ],
        rows: [
          CaseRowSchema.parse({ case: "a", name: "A", verdict: "fail", tries: ["failed"] }),
          CaseRowSchema.parse({ case: "b", name: "B", verdict: "pass", tries: ["passed"] }),
        ],
      }}
      onClose={vi.fn()}
      onStarted={vi.fn()}
    />,
  );
  expect(await screen.findByText(/as run 2 spent/)).toBeTruthy();
  expect(screen.getByText("Failing").parentElement?.textContent).toMatch(/Failing\s*1/);
  expect(runs).not.toHaveBeenCalled();
  expect(cases).not.toHaveBeenCalled();
});

it("holds the Run buttons only while the start is unanswered, not while a run plays", async () => {
  let answer: (value: Response) => void = () => undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) =>
      path.startsWith("/v1/evals/runs") && !path.includes("?")
        ? new Promise<Response>((resolve) => {
            answer = resolve;
          })
        : Promise.resolve(
            new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
          ),
    ),
  );
  const onStarting = vi.fn();
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 1,
    tries: 1,
    chats: 1,
    plays: [{ how: "api", cases: ["a"], seconds: 5, text: "" }],
  });
  render(
    <RunConfirm
      target={{
        scope: { target: "relay-lite", set: "main" },
        test: { id: "a", name: "A" },
        estimate,
      }}
      onClose={vi.fn()}
      onStarting={onStarting}
      onStarted={vi.fn()}
    />,
  );
  const go = await screen.findByRole("button", { name: "Run 1 test" });
  await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
  expect(onStarting).not.toHaveBeenCalled();
  fireEvent.click(go);
  expect(onStarting.mock.calls).toEqual([[true]]);
  answer(new Response(JSON.stringify({ detail: "no" }), { status: 500 }));
  await waitFor(() => expect(onStarting.mock.calls).toEqual([[true], [false]]));
});
