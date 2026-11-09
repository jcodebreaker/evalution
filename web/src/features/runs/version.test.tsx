// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import {
  ComparisonSchema,
  EstimateSchema,
  RunDetailSchema,
  RunSummarySchema,
} from "../../lib/schema";
import { Comparison } from "./Comparison";
import { RunConfirm } from "./RunConfirm";
import { RunRow } from "./RunRow";
import { RunView } from "./RunView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const HEAD = "e535c4d89abb688afc9be53a0dcc3291b2573a52";
const STALE =
  "relay/engine:local was built before the last commit (e535c4d), so it may be an earlier version";

const summary = (version: unknown) =>
  RunSummarySchema.parse({
    run: "r1",
    target: "relay-lite",
    set: "main",
    started_at: 10,
    status: "done",
    pass_marks: { tools: 0.8, reply: 0.7 },
    totals: { passed: 1, cases: 1, tools: null, reply: null, median_seconds: 0, tokens: 0 },
    version,
  });

const PLAYED = {
  ref: "disk",
  branch: "page-testing",
  commit: HEAD,
  dirty: true,
  verified: false,
  why_unverified: STALE,
  said: "what was on disk: page-testing @ e535c4d, with uncommitted changes",
};

const view = (version: unknown) => {
  vi.spyOn(api, "comparison").mockResolvedValue(ComparisonSchema.parse({ run: "r1", base: null }));
  const run = RunDetailSchema.parse({ ...summary(version), cases: [] });
  render(
    <RunView
      load={{ status: "ready", data: run }}
      runs={[]}
      label={(id) => `Run ${id}`}
      scopeLabel="All tests"
      going={false}
      onBack={() => {}}
      onBase={() => {}}
      onTest={() => {}}
      onRunTest={() => {}}
      onRunMore={() => {}}
      onStop={async () => {}}
      onAnalyze={async () => {}}
    />,
  );
};

it("says in the run's header what code it played, and warns when it can't vouch for it", () => {
  view(PLAYED);
  const details = within(screen.getByTestId("run-details"));
  expect(details.getByText("Code")).toBeTruthy();
  expect(
    details.getByText("What was on disk: page-testing @ e535c4d, with uncommitted changes"),
  ).toBeTruthy();
  const warning = screen.getByText(`Can't vouch for this: ${STALE}.`);
  expect(warning.className).toContain("text-warning");
});

it("says an old run's code as recorded, quietly, and nothing for a run from an older server", () => {
  view({
    commit: HEAD,
    verified: null,
    why_unverified: "not recorded: run before evalution kept what played",
    said: "what was on disk: e535c4d",
  });
  expect(screen.getByText("What was on disk: e535c4d")).toBeTruthy();
  expect(screen.getByText("Not recorded: run before evalution kept what played.")).toBeTruthy();
  cleanup();
  view(undefined);
  expect(screen.queryByTestId("code-lines")).toBeNull();
});

it("chips each run in the list with its branch and short commit", () => {
  render(<RunRow run={summary(PLAYED)} label="Run r1" onOpen={() => {}} />);
  const chip = screen.getByTestId("version-chip");
  expect(chip.textContent).toBe("page-testing @ e535c4d");
  expect(chip.getAttribute("title")).toBe(`Code: ${PLAYED.said}`);
  cleanup();
  // Not on a branch: the commit alone; no commit: no chip.
  const detached = summary({ ...PLAYED, branch: null });
  render(<RunRow run={detached} label="Run r1" onOpen={() => {}} />);
  expect(screen.getByTestId("version-chip").textContent).toBe("e535c4d");
  cleanup();
  render(<RunRow run={summary(null)} label="Run r1" onOpen={() => {}} />);
  expect(screen.queryByTestId("version-chip")).toBeNull();
});

it("says in the Run dialog what code the run will play, from the server", async () => {
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 1,
    tries: 1,
    chats: 1,
    version: {
      branch: "page-testing",
      commit: HEAD,
      dirty: true,
      verified: false,
      why_unverified: STALE,
      said: "what's on disk now: page-testing @ e535c4d, with uncommitted changes",
    },
  });
  render(
    <RunConfirm
      target={{ scope: { target: "relay-lite", set: "main" }, estimate }}
      onClose={() => null}
      onStarted={() => null}
    />,
  );
  await screen.findByText("What's on disk now: page-testing @ e535c4d, with uncommitted changes");
  expect(screen.getByText(`Can't vouch for this: ${STALE}.`)).toBeTruthy();
});

it("says plainly when the same code played under two commits", async () => {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({
      run: "r2",
      base: "r1",
      headline: "No change",
      code: "Same code played: the same images ran in both, though the checkout moved from a1b2c3d to e535c4d",
    }),
  );
  const run = { ...summary(PLAYED), run: "r2", started_at: 20 };
  render(
    <Comparison
      run={run}
      runs={[run, summary(PLAYED)]}
      label={(id) => `Run ${id}`}
      going={false}
      onTest={() => {}}
      onRunMore={() => {}}
    />,
  );
  const said = await screen.findByTestId("code-played");
  expect(said.textContent).toBe(
    "Same code played: the same images ran in both, though the checkout moved from a1b2c3d to e535c4d.",
  );
});
