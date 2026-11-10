// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { runNumbers } from "../../lib/format";
import { HealthSchema, RunDetailSchema, type RunSummary, RunSummarySchema } from "../../lib/schema";
import { type NeedsContext, NeedsYou } from "./NeedsYou";
import { PassingCard, SpentCard, TypicalCard } from "./NumberCards";
import { OfferRow, type PageOfferState, usePageOffer } from "./OfferRow";
import {
  grid,
  passingCard,
  rankNeeds,
  runOutcome,
  runReason,
  spentCard,
  typicalCard,
} from "./overview";
import { headerChips } from "./PageHeader";
import { RecentRuns } from "./RecentRuns";
import { RunGrid } from "./RunGrid";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type Test = { case: string; verdict: "pass" | "fail" | "flaky" | "unjudged" | null } & Record<
  string,
  unknown
>;

const NAMES: Record<string, string> = {
  code: "A code block shows",
  link: "A link shows",
  table: "A Markdown table shows",
  list: "A numbered list shows",
};

/** A stored run of the Page stress set, as the runs list sends it. */
function run(
  id: string,
  at: number,
  tests: Test[],
  extra: Record<string, unknown> = {},
  totals: Record<string, number> = {},
): RunSummary {
  return RunSummarySchema.parse({
    run: id,
    set: "page-stress",
    started_at: at,
    status: "done",
    pass_marks: { tools: 0.7, reply: 0.7 },
    totals: {
      passed: tests.filter((test) => test.verdict === "pass").length,
      cases: tests.length,
      tools: null,
      reply: null,
      median_seconds: 30,
      tokens: 100_000,
      ...totals,
    },
    tests: tests.map((test) => ({
      name: NAMES[test.case] ?? test.case,
      passed: test.verdict === "pass" ? 3 : 0,
      tries: test.verdict ? 3 : 0,
      ...test,
    })),
    progress: { done: tests.filter((test) => test.verdict).length * 3, total: 12, current: null },
    ...extra,
  });
}

const all = (verdict: Test["verdict"], cases = ["code", "link", "table", "list"]) =>
  cases.map((id) => ({ case: id, verdict }));

/** Runs 1 to 4 of a set, newest first: 1 passed, 2 never started, 3 passed, 4 lost three tests. */
function history(): { runs: RunSummary[]; numbers: Map<string, number> } {
  const runs = [
    run(
      "r4",
      400,
      [
        { case: "code", verdict: "fail", label: "No answer" },
        { case: "link", verdict: "fail" },
        { case: "table", verdict: "fail" },
        { case: "list", verdict: "pass" },
      ],
      {
        version: {
          branch: "evals-w0",
          commit: "e535c4d0123",
          verified: false,
          why_unverified: "its host image was built a day before e535c4d",
        },
        plays: "page",
      },
      { median_seconds: 123, tokens: 0 },
    ),
    run("r3", 300, all("pass"), { plays: "page" }, { median_seconds: 35, tokens: 103_000 }),
    run(
      "r2",
      200,
      all(null),
      {
        status: "failed",
        stop_reason: "The app answered 503\ntraceback",
        progress: { done: 0, total: 12, current: null },
      },
      { tokens: 0 },
    ),
    run(
      "r1",
      100,
      all("pass", ["code", "link", "table"]),
      {},
      { median_seconds: 13, tokens: 90_000 },
    ),
  ];
  return { runs, numbers: runNumbers(runs) };
}

/** Run 4's detail: compared with run 3, three tests got worse. */
function detailOf(runs: RunSummary[]) {
  const [now, base] = runs;
  return RunDetailSchema.parse({
    ...now,
    cases: now?.tests.map((test) => ({ case: test.case, name: test.name, verdict: test.verdict })),
    base,
    delta: { better: 0, worse: 3, changed: 0, tools: 0, reply: 0, median_seconds_pct: 0 },
  });
}

// ---------------------------------------------------------------- Needs you

it("ranks what needs you: what failed, then what may be wrong, then what's new", () => {
  const numbered = history();
  const health = HealthSchema.parse({
    page: { status: "stale", stale_files: ["web/chat.tsx"] },
    isolation: { status: "stale", stale_files: ["compose.yaml"] },
    attention: [
      // Said by the "got worse" row already.
      { title: "A link shows fails", action: "see_why", case: "link", run: "r4" },
      // Another set's run: not this Overview's.
      { title: "Something fails", action: "see_why", case: "x", run: "elsewhere" },
      {
        title: "A numbered list shows: couldn't judge",
        action: "see_why",
        case: "list",
        run: "r4",
      },
      { title: "2 tests may be out of date", action: "review" },
    ],
  });
  const needs = rankNeeds({ ...numbered, detail: detailOf(numbered.runs), health, offer: true });
  expect(needs.map((need) => [need.kind, need.tone])).toEqual([
    ["worse", "fail"],
    ["page", "unjudged"],
    ["version", "unjudged"],
    ["isolation", "unjudged"],
    ["attention", "unjudged"],
    ["attention", "unjudged"],
    ["offer", "new"],
  ]);
  const worse = needs[0];
  expect(worse?.kind === "worse" && worse.names).toEqual([
    "A code block shows",
    "A link shows",
    "A Markdown table shows",
  ]);
});

it("puts a run that stopped and a broken page map first, and says nothing when nothing needs you", () => {
  const stopped = run("r9", 900, all(null), {
    status: "failed",
    stop_reason: "The app wasn't ready",
    progress: { done: 0, total: 4, current: null },
  });
  const needs = rankNeeds({
    runs: [stopped],
    numbers: runNumbers([stopped]),
    detail: null,
    health: HealthSchema.parse({ page: { status: "broken", missing: ["button 'Send'"] } }),
    offer: true,
  });
  expect(needs.map((need) => need.kind)).toEqual(["stopped", "page", "offer"]);
  expect(
    rankNeeds({ runs: [], numbers: new Map(), detail: null, health: null, offer: false }),
  ).toEqual([]);
});

function context(extra: Partial<NeedsContext> = {}): NeedsContext {
  return {
    scope: { target: "relay-lite", set: "page-stress" },
    health: HealthSchema.parse({}),
    offer: {
      offer: null,
      reach: null,
      setOffer: () => undefined,
      setReach: () => undefined,
      read: async () => undefined,
      shows: false,
    },
    going: false,
    onRun: vi.fn(),
    onRunSome: vi.fn(),
    onGoing: vi.fn(),
    onTest: vi.fn(),
    onReview: vi.fn(),
    onWrite: vi.fn(),
    onChanged: vi.fn(),
    ...extra,
  };
}

it("each row has a dot, a bold title, a line and its one action, which works", () => {
  const numbered = history();
  const health = HealthSchema.parse({
    attention: [
      {
        title: "A numbered list shows keeps failing",
        detail: "No reply",
        action: "see_why",
        case: "list",
        run: "r4",
      },
    ],
    code_change: {
      areas: ["Markdown"],
      tests: [{ set: "page-stress", case: "table", name: "A Markdown table shows" }],
      uncovered: ["Images"],
      summary: "Rendering moved",
    },
  });
  const needs = rankNeeds({ ...numbered, detail: detailOf(numbered.runs), health, offer: false });
  const ctx = context({ health });
  render(<NeedsYou needs={needs} context={ctx} />);
  const card = screen.getByTestId("needs-you");
  expect(within(card).getByText("4")).toBeTruthy();

  const worse = screen.getByTestId("need-worse");
  expect(within(worse).getByText("3 tests got worse in run 4")).toBeTruthy();
  expect(
    within(worse).getByText(
      "A code block shows, A link shows and A Markdown table shows passed in run 3, the run it's compared with.",
    ),
  ).toBeTruthy();
  fireEvent.click(within(worse).getByRole("button", { name: "Open run 4" }));
  expect(ctx.onRun).toHaveBeenCalledWith("r4");

  const version = screen.getByTestId("need-version");
  expect(within(version).getByText("Runs may not have played this commit")).toBeTruthy();
  expect(within(version).getByText("Its host image was built a day before e535c4d.")).toBeTruthy();
  fireEvent.click(within(version).getByRole("button", { name: "See what played →" }));
  expect(ctx.onRun).toHaveBeenLastCalledWith("r4");

  const attention = screen.getByTestId("need-attention");
  fireEvent.click(within(attention).getByRole("button", { name: "See why" }));
  expect(ctx.onTest).toHaveBeenCalledWith("list", "r4");

  const change = screen.getByTestId("need-change");
  fireEvent.click(within(change).getByRole("button", { name: "Run them" }));
  expect(ctx.onRunSome).toHaveBeenCalledWith("page-stress", ["table"]);
  fireEvent.click(within(change).getByRole("button", { name: "Images" }));
  expect(ctx.onWrite).toHaveBeenCalledWith(
    "Propose a test for Images. What changed: Rendering moved",
  );

  // Failures first (the run's, then the test's), then what may be wrong.
  expect(screen.getAllByRole("listitem").map((row) => row.getAttribute("data-tone"))).toEqual([
    "fail",
    "fail",
    "unjudged",
    "unjudged",
  ]);
});

it("the change row reads the one changes signal the learned page reads too", () => {
  const health = HealthSchema.parse({
    code_change: { areas: [], tests: [], file_count: 9, read: false },
    changes: {
      files: ["lib/b.py"],
      file_count: 1,
      areas: [],
      tests: [{ set: "main", case: "a", name: "A" }],
      since: { tested: { commit: "abc", files: ["lib/b.py"], file_count: 1 } },
    },
  });
  const needs = rankNeeds({ runs: [], numbers: new Map(), detail: null, health, offer: false });
  expect(needs.map((need) => need.kind)).toEqual(["change"]);
  render(<NeedsYou needs={needs} context={context({ health })} />);
  const change = screen.getByTestId("need-change");
  expect(within(change).getByText("Code changed since the last run: 1 file")).toBeTruthy();
  expect(within(change).getByText(/1 test affected/)).toBeTruthy();
});

it("says in one quiet line when nothing needs you", () => {
  render(<NeedsYou needs={[]} context={context()} />);
  expect(screen.getByTestId("needs-you-empty").textContent).toBe("Nothing needs you right now.");
});

type Call = { method: string; path: string };

function serve(routes: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, path });
      const found = routes[`${method} ${path.split("?")[0]}`];
      return new Response(JSON.stringify(found ?? {}), {
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

function Offer({ seen }: { seen?: (state: PageOfferState) => void }) {
  const state = usePageOffer("relay-lite");
  seen?.(state);
  return (
    <ul>
      <OfferRow target="relay-lite" state={state} />
    </ul>
  );
}

const T = "/v1/targets/relay-lite";

it("offers UI testing as a New row: Set up starts the agent, the demo is a line away", async () => {
  const calls = serve({
    [`GET ${T}/page/offer`]: { target: "relay-lite", offer: true, can_learn: true },
    [`POST ${T}/page/learn`]: {
      target: "relay-lite",
      plays: "api",
      learn: {
        state: "running",
        started: 1,
        steps: [{ key: "code", label: "Read the code", state: "running" }],
      },
    },
  });
  render(<Offer />);
  const row = await screen.findByTestId("ui-offer");
  expect(within(row).getByText("New")).toBeTruthy();
  expect(within(row).getByText("The agent can learn relay-lite's page")).toBeTruthy();
  fireEvent.click(within(row).getByRole("button", { name: "Try the demo first" }));
  expect(screen.getByTestId("ui-demo").hidden).toBe(false);
  fireEvent.click(within(row).getByRole("button", { name: "Set up UI testing" }));
  await screen.findByText("The agent is learning relay-lite's page");
  expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual([
    `${T}/page/learn`,
  ]);
});

it("Not now turns the offer down on the server, and the row goes", async () => {
  const calls = serve({
    [`GET ${T}/page/offer`]: { target: "relay-lite", offer: true, can_learn: true },
    [`POST ${T}/page/offer/dismiss`]: { target: "relay-lite", offer: false, dismissed: true },
  });
  let shows = true;
  render(<Offer seen={(state) => (shows = state.shows)} />);
  const row = await screen.findByTestId("ui-offer");
  fireEvent.click(within(row).getByRole("button", { name: "Not now" }));
  await waitFor(() => expect(screen.queryByTestId("ui-offer")).toBeNull());
  expect(shows).toBe(false);
  expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual([
    `${T}/page/offer/dismiss`,
  ]);
});

// ---------------------------------------------------------------- the numbers

it("counts the last run's tests passing against its base, with a bar per run by outcome", () => {
  const numbered = history();
  const card = passingCard(numbered, detailOf(numbered.runs));
  expect(card).toMatchObject({ n: 4, passed: 1, total: 4, delta: { by: -3, n: 3 } });
  expect(card?.bars.map((bar) => [bar.n, bar.tone])).toEqual([
    [1, "pass"],
    [2, "none"],
    [3, "pass"],
    [4, "fail"],
  ]);
  render(card ? <PassingCard card={card} /> : null);
  const shown = screen.getByTestId("kpi-passing");
  expect(within(shown).getByText("1 of 4")).toBeTruthy();
  expect(within(shown).getByText("−3 vs run 3")).toBeTruthy();
  expect(shown.querySelectorAll("rect")).toHaveLength(4);
});

it("says the typical try of the last run that played, and what each run spent", () => {
  const numbered = history();
  const typical = typicalCard(numbered);
  // Run 2 played no try: it has no point.
  expect(typical?.points.map((point) => point?.value ?? null)).toEqual([13, null, 35, 123]);
  render(typical ? <TypicalCard card={typical} /> : null);
  const shownTypical = screen.getByTestId("kpi-typical");
  expect(within(shownTypical).getByText("123 s")).toBeTruthy();
  expect(within(shownTypical).getByText("88 s slower than run 3")).toBeTruthy();

  const spent = spentCard(numbered);
  expect(spent).toMatchObject({ n: 4, tokens: 0, typical: 96_500 });
  render(spent ? <SpentCard card={spent} /> : null);
  const shownSpent = screen.getByTestId("kpi-spent");
  expect(within(shownSpent).getByText("Run 4 spent nothing")).toBeTruthy();
  expect(shownSpent.querySelectorAll("rect")).toHaveLength(4);
});

it("has nothing to count before a run has finished", () => {
  const playing = run("r1", 100, all(null), { status: "running" });
  const numbered = { runs: [playing], numbers: runNumbers([playing]) };
  expect(passingCard(numbered, null)).toBeNull();
  expect(typicalCard(numbered)).toBeNull();
  expect(spentCard(numbered)).toBeNull();
});

// ---------------------------------------------------------------- the grid

it("lays out each test run by run: passed, failed, not in the run, a run that didn't start", () => {
  const numbered = history();
  const found = grid(numbered);
  expect(found.columns.map((column) => [column.n, column.latest])).toEqual([
    [1, false],
    [2, false],
    [3, false],
    [4, true],
  ]);
  const list = found.rows.find((row) => row.case === "list");
  expect(list?.cells.map((cell) => cell.state)).toEqual(["none", "didnt_start", "pass", "pass"]);
  expect(list?.cells.map((cell) => cell.title)).toEqual([
    "Run 1: not in this run",
    "Run 2: didn't start (The app answered 503)",
    "Run 3: passed",
    "Run 4: passed",
  ]);
  const code = found.rows.find((row) => row.case === "code");
  expect(code?.cells[3]?.title).toBe("Run 4: no answer");

  const onTest = vi.fn();
  const onRun = vi.fn();
  render(<RunGrid grid={found} setName="page-stress" onTest={onTest} onRun={onRun} />);
  fireEvent.click(screen.getByRole("button", { name: "Run 4: no answer" }));
  expect(onTest).toHaveBeenCalledWith("code", "r4");
  fireEvent.click(screen.getAllByRole("button", { name: /^Run 2: didn't start/ })[0] as Element);
  expect(onRun).toHaveBeenCalledWith("r2");
  // The latest run's cells are the ones outlined.
  const latest = document.querySelectorAll("[data-latest]");
  expect(latest).toHaveLength(4);
  expect(screen.getByText("Run didn't start")).toBeTruthy();
});

// ---------------------------------------------------------------- recent runs and the header

it("lists recent runs with a dot, a reason, the result, the code and the numbers", () => {
  const numbered = history();
  expect(numbered.runs.map(runOutcome)).toEqual(["fail", "pass", "none", "pass"]);
  expect(numbered.runs.map(runReason)).toEqual([
    "A code block shows, A link shows and A Markdown table shows failed",
    "All passed",
    "Stopped: The app answered 503",
    "All passed",
  ]);
  const onRun = vi.fn();
  const onRuns = vi.fn();
  render(
    <RecentRuns runs={numbered.runs} numbers={numbered.numbers} onRun={onRun} onRuns={onRuns} />,
  );
  const rows = screen.getAllByTestId("recent-run");
  expect(rows).toHaveLength(4);
  const first = rows[0] as HTMLElement;
  expect(within(first).getByText("1 of 4")).toBeTruthy();
  expect(within(first).getByText("evals-w0 @ e535c4d")).toBeTruthy();
  expect(within(first).getByText("123 s")).toBeTruthy();
  expect(within(rows[2] as HTMLElement).getByText("didn't start")).toBeTruthy();
  fireEvent.click(within(first).getByRole("button", { name: "Run 4" }));
  expect(onRun).toHaveBeenCalledWith("r4");
  fireEvent.click(screen.getByRole("button", { name: "All 4 runs →" }));
  expect(onRuns).toHaveBeenCalled();
});

it("puts in the header only the chips a run says", () => {
  const numbered = history();
  const newest = numbered.runs[0] ?? null;
  expect(headerChips(newest, null).map((chip) => chip.text)).toEqual([
    "evals-w0 @ e535c4d",
    "Through its page",
  ]);
  const proven = RunSummarySchema.parse({
    ...newest,
    isolation: { how: "proven", plan_version: 2 },
  });
  expect(headerChips(proven, null)[0]?.text).toBe(
    "Plays on evalution's copy, reset before every try",
  );
  expect(headerChips(null, null)).toEqual([]);
});
