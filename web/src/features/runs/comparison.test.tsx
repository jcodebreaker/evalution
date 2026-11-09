// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { ComparisonSchema, RunSummarySchema } from "../../lib/schema";
import { followRun } from "../shell/use-shown";
import { Comparison } from "./Comparison";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const run = (id: string, set: string, started_at: number) =>
  RunSummarySchema.parse({
    run: id,
    target: "relay-lite",
    set,
    started_at,
    status: "done",
    pass_marks: { tools: 0.8, reply: 0.7 },
    totals: { passed: 0, cases: 1, tools: null, reply: null, median_seconds: 0, tokens: 0 },
  });

const SHOWN = run("stress-3", "page-stress", 30);
const PREVIOUS = run("stress-2", "page-stress", 20);
const FIRST = run("stress-1", "page-stress", 10);
const OTHER_SET = run("smoke-5", "isolation-smoke", 25);

const show = (runs = [OTHER_SET]) =>
  render(
    <Comparison
      run={SHOWN}
      runs={runs}
      label={(id) => `Run ${id}`}
      going={false}
      onTest={() => {}}
      onRunMore={() => {}}
    />,
  );

it("compares only with earlier runs of the viewed run's own set", async () => {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({ run: SHOWN.run, base: PREVIOUS.run, headline: "3 got worse" }),
  );
  show([SHOWN, OTHER_SET, PREVIOUS, FIRST, run("stress-4", "page-stress", 40)]);
  await screen.findByText("3 got worse");
  const select = screen.getByRole("combobox") as HTMLSelectElement;
  expect(select.value).toBe(PREVIOUS.run);
  expect([...select.options].map((option) => option.value)).toEqual([PREVIOUS.run, FIRST.run]);
});

it("says so when the set has no earlier run, and offers no other set's", async () => {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({ run: SHOWN.run, base: null }),
  );
  show();
  await screen.findByText("No earlier run of this test set to compare with.");
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("a run opened from another set's page brings the page to the run's own set", () => {
  const place = { target: null, set: null, run: SHOWN.run, view: "runs" as const };
  expect(followRun(place, SHOWN, { target: "relay-lite", set: "isolation-smoke" })).toEqual({
    target: "relay-lite",
    set: "page-stress",
  });
  expect(followRun(place, SHOWN, { target: "relay-lite", set: "page-stress" })).toBeNull();
  expect(followRun({ ...place, run: "another" }, SHOWN, { target: null, set: null })).toBeNull();
});

it("reads plainly: what moved, what stayed, and a test the run never played", async () => {
  const side = (verdict: string, passed: number, judged: number) => ({ verdict, passed, judged });
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({
      run: SHOWN.run,
      base: PREVIOUS.run,
      headline: "1 test got worse",
      tests: [
        {
          case: "table",
          name: "A Markdown table shows",
          change: "worse",
          before: side("pass", 1, 1),
          now: side("fail", 0, 1),
        },
        {
          case: "list",
          name: "A numbered list shows",
          change: "unknown",
          before: side("pass", 1, 1),
          now: side("not_run", 0, 0),
        },
        ...["a", "b"].map((id) => ({
          case: id,
          name: id,
          change: "same",
          before: side("pass", 1, 1),
          now: side("pass", 1, 1),
        })),
        {
          case: "c",
          name: "c",
          change: "same",
          before: side("fail", 0, 1),
          now: side("fail", 0, 1),
        },
      ],
    }),
  );
  show([SHOWN, PREVIOUS]);
  await screen.findByText("1 test got worse");
  expect(screen.getByText("1 of 1 → 0 of 1")).toBeTruthy();
  expect(screen.getByText("Not run")).toBeTruthy();
  expect(screen.getByText("1 of 1 → not run")).toBeTruthy();
  expect(screen.getByText("2 tests pass, as before; 1 test still fails")).toBeTruthy();
  expect(
    screen.getByText(
      "Same setup as that run: no recorded change in model, code, prompt or grader.",
    ),
  ).toBeTruthy();
});
