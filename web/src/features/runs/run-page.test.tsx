// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import {
  CaseRowSchema,
  ComparisonSchema,
  EstimateSchema,
  RunDetailSchema,
  RunSummarySchema,
} from "../../lib/schema";
import { Comparison } from "./Comparison";
import { Results } from "./Results";
import { RunConfirm } from "./RunConfirm";
import { RunsView } from "./RunsView";
import { RunView } from "./RunView";

/** The redesigned run page (docs/mockups/design-studio.html, "A RUN"), from a run's own data. */

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const BASE = {
  target: "relay-lite",
  set: "page-stress",
  pass_marks: { tools: 0.8, reply: 0.7 },
};

const REASON = "the app answered a request from the page with an error, 503";
const failed = (id: string, name: string) => ({
  case: id,
  name,
  verdict: "fail",
  tries: ["failed"],
  plays: "page",
  reasons: [REASON],
  seconds: 123,
  layers: { stored: "absent", answered: "unjudged", shown: "unjudged", page_health: "fail" },
});

/** Run 9 of the mockup: stopped early, 3 failed every try, 1 not run. */
const RUN9 = {
  ...BASE,
  run: "r9",
  started_at: 1000,
  ended_at: 1360,
  status: "failed",
  stop_reason: "3 tries in a row ran out of time with no answer at all.",
  plays: "page",
  isolation: { how: "proven", plan_version: 2 },
  version: {
    ref: "disk",
    branch: "evals-w0",
    commit: "e535c4d89abb",
    verified: false,
    why_unverified: "relay/host:local was built before the last commit (e535c4d)",
    said: "what was on disk: evals-w0 @ e535c4d",
    played: {
      kind: "images",
      images: [
        { service: "engine", image: "relay/engine:local", built: true },
        { service: "db", image: "postgres:16", built: false },
      ],
    },
  },
  totals: { passed: 0, cases: 4, tools: null, reply: null, median_seconds: 123, tokens: 0 },
  tests: [
    { case: "code", name: "A code block shows", verdict: "fail", passed: 0, tries: 1 },
    { case: "link", name: "A link shows", verdict: "fail", passed: 0, tries: 1 },
    { case: "table", name: "A Markdown table shows", verdict: "fail", passed: 0, tries: 1 },
    {
      case: "list",
      name: "A numbered list shows",
      verdict: null,
      passed: 0,
      tries: 0,
      reason: "the run stopped first",
    },
  ],
  cases: [
    failed("code", "A code block shows"),
    failed("link", "A link shows"),
    failed("table", "A Markdown table shows"),
    { case: "list", name: "A numbered list shows", verdict: null, plays: "page" },
  ],
};

function showRun(run: unknown, more: Partial<Parameters<typeof RunView>[0]> = {}) {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({ run: (run as { run: string }).run, base: null }),
  );
  const onRunMore = vi.fn();
  const onRunOn = vi.fn();
  render(
    <RunView
      load={{ status: "ready", data: RunDetailSchema.parse(run) }}
      runs={[]}
      label={(id) => `Run ${id.slice(1)}`}
      scopeLabel="All tests"
      going={false}
      onBack={() => {}}
      onBase={() => {}}
      onTest={() => {}}
      onRunTest={() => {}}
      onRunMore={onRunMore}
      onRunOn={onRunOn}
      onStop={async () => {}}
      onAnalyze={async () => {}}
      {...more}
    />,
  );
  return { onRunMore, onRunOn };
}

it("heads a run with its status, its facts as plain chips, and its two actions", () => {
  const { onRunMore, onRunOn } = showRun(RUN9);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Run 9");
  expect(screen.getByTestId("run-state").textContent).toBe("Stopped early");
  const chips = screen.getByTestId("run-chips");
  const said = [...chips.children].map((chip) => chip.textContent);
  expect(said[0]).toMatch(/ · 6 min$/);
  expect(said.slice(1)).toEqual([
    "evals-w0 @ e535c4d",
    "Clean copy · plan v2",
    "Through the page, one at a time",
  ]);
  // Plain words: no icon on any chip.
  expect(chips.querySelector("svg")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Run with 5 tries" }));
  expect(onRunMore).toHaveBeenCalledWith(["code", "link", "table"], 5);
  fireEvent.click(screen.getByRole("button", { name: "Run again" }));
  expect(onRunOn).toHaveBeenCalledWith("page-stress", "disk");
});

it("says how many pass as a ring, a bar and counts, and why the run stopped", () => {
  showRun(RUN9);
  const result = within(screen.getByTestId("run-result"));
  expect(result.getByRole("heading", { level: 2 }).textContent).toBe("0 of 4 tests pass");
  expect(result.getByRole("img", { name: "0 of 4 passed" })).toBeTruthy();
  expect(result.getByText("3 failed every try")).toBeTruthy();
  expect(result.getByText("1 not run")).toBeTruthy();
  expect(result.getByText("0 tokens")).toBeTruthy();
  expect(screen.getByTestId("why-stopped").textContent).toBe(
    "Why it stopped: 3 tries in a row ran out of time with no answer at all.",
  );
});

it("lists the run's details: what played from its images, its copy and how it plays", () => {
  showRun(RUN9);
  const details = within(screen.getByTestId("run-details"));
  expect(details.getByText("relay-lite")).toBeTruthy();
  expect(details.getByText("page-stress · all 4")).toBeTruthy();
  // Only the images the app's own files build hold its code.
  expect(details.getByText("relay/engine:local")).toBeTruthy();
  expect(details.getByText(/Can't vouch for this: relay\/host:local/).className).toContain(
    "text-warning",
  );
});

it("runs again the same tests, with the tries each had, for a run of some of the set", () => {
  const some = {
    ...RUN9,
    status: "done",
    stop_reason: null,
    scope: "some",
    cases: RUN9.cases.slice(0, 2).map((row) => ({ ...row, tries: ["failed", "passed"] })),
    tests: RUN9.tests.slice(0, 2),
    totals: { ...RUN9.totals, cases: 2 },
  };
  const { onRunMore, onRunOn } = showRun(some);
  expect(screen.getByTestId("run-state").textContent).toBe("Failed");
  expect(screen.queryByTestId("why-stopped")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Run again" }));
  expect(onRunMore).toHaveBeenCalledWith(["code", "link"], 2);
  expect(onRunOn).not.toHaveBeenCalled();
});

it("says a run that passed as passed, and offers no tries for tests that didn't fail", () => {
  showRun({
    ...RUN9,
    status: "done",
    stop_reason: null,
    cases: [{ case: "a", name: "A", verdict: "pass", tries: ["passed"] }],
    tests: [{ case: "a", name: "A", verdict: "pass", passed: 1, tries: 1 }],
    totals: { ...RUN9.totals, passed: 1, cases: 1, tokens: 103000 },
  });
  expect(screen.getByTestId("run-state").textContent).toBe("Passed");
  expect(screen.getByText("103k tokens")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Run with 5 tries" })).toBeNull();
});

it("shows results by tab, each layer as a dot column, and why a test didn't play", () => {
  const rows = RUN9.cases.map((row) => CaseRowSchema.parse(row));
  const onTest = vi.fn();
  render(
    <Results
      rows={rows}
      going={false}
      onTest={onTest}
      onRun={() => {}}
      why={(id) => (id === "list" ? "the run stopped first" : null)}
    />,
  );
  const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
  expect(tabs).toEqual(["All4", "Failed3", "Not run1"]);
  const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
  expect(headers.slice(0, 7)).toEqual([
    "Test",
    "Stored",
    "Answered",
    "Shown",
    "Page healthy",
    "Tries",
    "Time",
  ]);
  const code = within(screen.getByText("A code block shows").closest("tr") as HTMLElement);
  expect(code.getByRole("img", { name: "Answered: Couldn't judge" })).toBeTruthy();
  expect(code.getByRole("img", { name: "Page healthy: Failed" })).toBeTruthy();
  expect(code.getByTitle("Stored: not checked").textContent).toBe("–");
  expect(
    code.getByText("The app answered a request from the page with an error, 503"),
  ).toBeTruthy();
  expect(code.getByText("123 s")).toBeTruthy();
  expect(within(screen.getByTestId("layer-key")).getByText("Held")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: /Not run/ }));
  expect(screen.queryByText("A code block shows")).toBeNull();
  expect(screen.getByText("The run stopped first")).toBeTruthy();
  fireEvent.click(screen.getByText("A numbered list shows"));
  expect(onTest).toHaveBeenCalledWith("list");
});

it("shows no layer columns for a run played through the API alone", () => {
  const rows = [CaseRowSchema.parse({ case: "a", name: "A", verdict: "pass", tries: ["passed"] })];
  render(<Results rows={rows} going={false} onTest={() => {}} onRun={() => {}} />);
  expect(screen.queryByRole("columnheader", { name: "Stored" })).toBeNull();
  expect(screen.queryByTestId("layer-key")).toBeNull();
  expect(screen.queryByRole("tablist")).toBeNull();
});

it("draws what changed as diff rows, and runs the moved tests again with more tries", async () => {
  const side = (verdict: string, passed: number) => ({ verdict, passed, judged: 1 });
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({
      run: "r9",
      base: "r8",
      headline: "3 got worse",
      could_be_chance: true,
      tests: [
        {
          case: "code",
          name: "A code block shows",
          change: "worse",
          before: side("pass", 1),
          now: side("fail", 0),
        },
      ],
    }),
  );
  const onRunMore = vi.fn();
  const summary = RunSummarySchema.parse({ ...RUN9, cases: [] });
  render(
    <Comparison
      run={summary}
      runs={[summary, RunSummarySchema.parse({ ...RUN9, run: "r8", started_at: 900, cases: [] })]}
      label={(id) => `Run ${id.slice(1)}`}
      going={false}
      onTest={() => {}}
      onRunMore={onRunMore}
    />,
  );
  await screen.findByText("3 got worse");
  const row = screen.getByText("A code block shows").closest("li") as HTMLElement;
  expect(row.dataset.change).toBe("worse");
  expect(within(row).getByText("−")).toBeTruthy();
  expect(within(row).getByText("Worse")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Run it with 5 tries" }));
  expect(onRunMore).toHaveBeenCalledWith(["code"], 5);
});

it("lists each run with its dot, why in a line, a bar, its typical try and tokens", () => {
  const runs = [
    RunSummarySchema.parse({ ...RUN9, cases: [] }),
    RunSummarySchema.parse({
      ...BASE,
      run: "r8",
      started_at: 900,
      status: "done",
      totals: { passed: 4, cases: 4, tools: null, reply: null, median_seconds: 35, tokens: 103000 },
    }),
  ];
  const onOpen = vi.fn();
  render(
    <RunsView runs={runs} label={(id) => `Run ${id.slice(1)}`} empty={null} onOpen={onOpen} />,
  );
  const nine = within(screen.getByRole("button", { name: /Run 9/ }));
  expect(nine.getByRole("img", { name: "Stopped early" })).toBeTruthy();
  expect(
    nine.getByText("Stopped: 3 tries in a row ran out of time with no answer at all."),
  ).toBeTruthy();
  expect(nine.getByText("0 of 4")).toBeTruthy();
  expect(nine.getByText("123 s")).toBeTruthy();
  const eight = within(screen.getByRole("button", { name: /Run 8/ }));
  expect(eight.getByText("All passed")).toBeTruthy();
  expect(eight.getByText("103k")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Run 8/ }));
  expect(onOpen).toHaveBeenCalledWith("r8", null);
});

it("says the run before in plain words, with the tokens the set's last full run spent", async () => {
  vi.spyOn(api, "runs").mockResolvedValue([
    RunSummarySchema.parse({
      ...BASE,
      run: "r1",
      started_at: 1,
      status: "done",
      totals: { passed: 4, cases: 4, tools: null, reply: null, median_seconds: 35, tokens: 1000 },
    }),
    RunSummarySchema.parse({
      ...BASE,
      run: "r2",
      started_at: 2,
      status: "done",
      totals: { passed: 4, cases: 4, tools: null, reply: null, median_seconds: 35, tokens: 103000 },
    }),
    RunSummarySchema.parse({ ...RUN9, run: "r3", started_at: 3, cases: [] }),
  ]);
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 4,
    tries: 4,
    chats: 4,
    plays: [{ how: "page", cases: ["a", "b", "c", "d"], seconds: 35, text: "" }],
    isolation: { how: "proven", plan_version: 2, status: "proven", start_seconds: 81 },
  });
  render(
    <RunConfirm
      target={{ scope: { target: "relay-lite", set: "page-stress" }, estimate }}
      onClose={() => {}}
      onStarted={() => {}}
    />,
  );
  await screen.findByText(/as run 2 spent/);
  expect(screen.getByTestId("run-estimate").textContent).toBe(
    "About 4 min · starts the copy (about 81 s), then 4 tries · about 103k tokens, as run 2 spent",
  );
  expect(screen.getByText(/^All 4 tests · 1 try each · /)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Run 4 tests" })).toBeTruthy();
});
