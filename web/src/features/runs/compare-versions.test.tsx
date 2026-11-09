// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { ComparisonSchema, RunSummarySchema } from "../../lib/schema";
import { Comparison } from "./Comparison";
import { RunsView, versionsOf } from "./RunsView";

/** Comparing across versions (ADR 0011, phase 6): "feature-x vs main", a way to compare with or
 * run the base branch, and the runs list by version. */

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const run = (id: string, started_at: number, branch: string | null, commit: string | null) =>
  RunSummarySchema.parse({
    run: id,
    target: "relay-lite",
    set: "smoke",
    started_at,
    status: "done",
    pass_marks: { tools: 0.8, reply: 0.7 },
    totals: { passed: 1, cases: 1, tools: null, reply: null, median_seconds: 0, tokens: 0 },
    tests: [{ case: "greets", name: "Greets", verdict: "pass", passed: 1, tries: 1 }],
    version: commit
      ? { ref: branch === "main" ? "disk" : (branch ?? "disk"), branch, commit, said: "" }
      : null,
  });

const FEATURE = run("f2", 40, "feature-x", "4444444aaaa");
const FEATURE_OLD = run("f1", 30, "feature-x", "3333333aaaa");
const MAIN = run("m1", 20, "main", "2222222aaaa");
const OLD = run("o1", 10, null, null);

const show = (onRunOn = vi.fn()) => {
  render(
    <Comparison
      run={FEATURE}
      runs={[FEATURE, FEATURE_OLD, MAIN]}
      label={(id) => `Run ${id}`}
      going={false}
      onTest={() => {}}
      onRunMore={() => {}}
      onRunOn={onRunOn}
    />,
  );
  return onRunOn;
};

const VERSIONS = {
  now: { name: "feature-x", said: "feature-x @ 4444444" },
  before: { name: "main", said: "what was on disk: main @ 2222222" },
  heading: "feature-x vs main",
};

it("says which versions it compares: feature-x vs main", async () => {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({
      run: FEATURE.run,
      base: MAIN.run,
      headline: "No change",
      versions: VERSIONS,
      base_branch: { name: "main", run: MAIN.run },
    }),
  );
  show();
  const heading = await screen.findByTestId("compare-versions");
  expect(heading.textContent).toBe("feature-x vs main");
  expect(heading.getAttribute("title")).toBe(
    "feature-x @ 4444444 vs what was on disk: main @ 2222222",
  );
  // Compared with main already: nothing to offer.
  expect(screen.queryByTestId("compare-with-base")).toBeNull();
});

it("offers to compare with main when another run is picked", async () => {
  const compare = vi.spyOn(api, "comparison").mockImplementation(async (_run, base) =>
    ComparisonSchema.parse({
      run: FEATURE.run,
      base: base ?? FEATURE_OLD.run,
      headline: base ? "Compared with main" : "Compared with feature-x",
      base_branch: { name: "main", run: MAIN.run },
    }),
  );
  show();
  await screen.findByText("Compared with feature-x");
  fireEvent.click(screen.getByRole("button", { name: "Compare with main" }));
  await screen.findByText("Compared with main");
  expect(compare).toHaveBeenLastCalledWith(FEATURE.run, MAIN.run);
});

it("says when there's no run of these tests on main, and offers to run them there", async () => {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({
      run: FEATURE.run,
      base: FEATURE_OLD.run,
      headline: "No change",
      base_branch: { name: "main", run: null, missing: "No run of these tests on main yet" },
    }),
  );
  const onRunOn = show();
  await screen.findByText("No run of these tests on main yet.");
  fireEvent.click(screen.getByRole("button", { name: "Run them on main" }));
  expect(onRunOn).toHaveBeenCalledWith("main");
});

it("with nothing at all to compare with, still offers main", async () => {
  vi.spyOn(api, "comparison").mockResolvedValue(
    ComparisonSchema.parse({
      run: FEATURE.run,
      base: null,
      base_branch: { name: "main", run: null, missing: "No run of these tests on main yet" },
    }),
  );
  const onRunOn = show();
  await screen.findByText("No earlier run of this test set to compare with.");
  fireEvent.click(screen.getByRole("button", { name: "Run them on main" }));
  expect(onRunOn).toHaveBeenCalledWith("main");
});

it("filters the runs list by version", () => {
  const runs = [FEATURE, FEATURE_OLD, MAIN, OLD];
  expect(versionsOf(runs)).toEqual([
    "feature-x @ 4444444",
    "feature-x @ 3333333",
    "main @ 2222222",
  ]);
  render(<RunsView runs={runs} label={(id) => `Run ${id}`} empty={null} onOpen={() => {}} />);
  const select = screen.getByRole("combobox", { name: "Version" }) as HTMLSelectElement;
  expect([...select.options].map((option) => option.text)).toEqual([
    "All versions",
    "feature-x @ 4444444",
    "feature-x @ 3333333",
    "main @ 2222222",
  ]);
  expect(screen.getAllByText(/^Run /).map((row) => row.textContent)).toEqual([
    "Run f2",
    "Run f1",
    "Run m1",
    "Run o1",
  ]);
  fireEvent.change(select, { target: { value: "main @ 2222222" } });
  expect(screen.getAllByText(/^Run /).map((row) => row.textContent)).toEqual(["Run m1"]);
  expect(within(screen.getByRole("button", { name: /Run m1/ })).getByText("main @ 2222222"));
  fireEvent.change(select, { target: { value: "" } });
  expect(screen.getAllByText(/^Run /)).toHaveLength(4);
});

it("shows no version filter when every run played the same version", () => {
  render(
    <RunsView runs={[MAIN, OLD]} label={(id) => `Run ${id}`} empty={null} onOpen={() => {}} />,
  );
  expect(screen.queryByRole("combobox", { name: "Version" })).toBeNull();
});
