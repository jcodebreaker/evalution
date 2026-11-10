// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Understanding } from "../../lib/schema";
import { CaseRowSchema, RunSummarySchema, UnderstandingSchema } from "../../lib/schema";
import { suggestedAsk } from "./Suggested";
import { lastRuns, TestsView } from "./TestsView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const row = (fields: Record<string, unknown>) => CaseRowSchema.parse({ verdict: null, ...fields });

const ROWS = [
  row({ case: "order", name: "Finds the right order", verdict: "pass", tries: ["passed"] }),
  row({
    case: "size",
    name: "Remembers a shoe size",
    about: "A size given once is used later",
    verdict: "fail",
    tries: ["failed", "failed", "passed"],
    seconds: 6.2,
  }),
  row({ case: "cart", name: "Adds to the cart once", verdict: "flaky", stale: "cart.ts moved" }),
];

const run = (id: string, at: number, verdicts: Record<string, string>) =>
  RunSummarySchema.parse({
    run: id,
    set: "main",
    started_at: at,
    status: "done",
    pass_marks: { tools: 0.8, reply: 0.7 },
    totals: { passed: 0, cases: 0, tools: null, reply: null, median_seconds: 0, tokens: 0 },
    tests: Object.entries(verdicts).map(([test, verdict]) => ({
      case: test,
      name: test,
      verdict,
      passed: 0,
      tries: 1,
    })),
  });

function open({ going = false, onRun = vi.fn(), onAsk = vi.fn(async () => undefined) } = {}) {
  vi.spyOn(api, "runs").mockResolvedValue([
    run("r2", 200, { size: "fail", order: "pass" }),
    run("r1", 100, { size: "pass" }),
  ]);
  vi.spyOn(api, "sets").mockResolvedValue([]);
  vi.spyOn(api, "understanding").mockResolvedValue({
    starters: UnderstandingSchema.shape.starters.parse([
      { id: "s1", title: "Checkout stops on an empty cart", what: "checkout/ has no test" },
      { id: "s2", title: "Already there", exists: true },
    ]),
  } as unknown as Understanding);
  render(
    <TestsView
      scope={{ target: "cartly", set: "main" }}
      rows={ROWS}
      passMarks={{ tools: 0.8, reply: 0.7 }}
      going={going}
      onAsk={onAsk}
      onPassMarks={vi.fn(async () => undefined)}
      onGoing={vi.fn()}
      onRunAll={vi.fn()}
      onRun={onRun}
      onOpen={vi.fn()}
    />,
  );
  return { onRun, onAsk };
}

describe("The tests list", () => {
  it("has a run button on every row, always there, not only on hover", () => {
    const { onRun } = open();
    const buttons = ROWS.map((entry) => screen.getByRole("button", { name: `Run ${entry.name}` }));
    for (const button of buttons) {
      expect(button.className).not.toMatch(/opacity-0/);
    }
    fireEvent.click(buttons[1] as HTMLElement);
    expect(onRun).toHaveBeenCalledWith(ROWS[1]);
  });

  it("keeps the run buttons while a run goes, but they can't start another", () => {
    open({ going: true });
    const button = screen.getByRole("button", { name: "Run Finds the right order" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("filters to the failing, flaky or maybe stale, with their counts, and finds by name", () => {
    open();
    const show = screen.getByRole("group", { name: "Show" });
    expect(within(show).getByRole("button", { name: "Failing 1" })).toBeTruthy();
    fireEvent.click(within(show).getByRole("button", { name: "Flaky 1" }));
    expect(screen.getAllByTestId("test-row")).toHaveLength(1);
    expect(screen.getByText("Adds to the cart once")).toBeTruthy();
    fireEvent.click(within(show).getByRole("button", { name: "All 3" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Find a test" }), {
      target: { value: "shoe" },
    });
    expect(screen.getAllByTestId("test-row")).toHaveLength(1);
    expect(screen.getByText("Remembers a shoe size")).toBeTruthy();
  });

  it("shows each test's last runs as cells, its tries and its time", async () => {
    open();
    await waitFor(() =>
      expect(
        screen
          .getAllByRole("img", { name: "Last runs of Remembers a shoe size" })[0]
          ?.querySelectorAll("[data-cell='pass'], [data-cell='fail']").length,
      ).toBe(2),
    );
    const size = screen.getAllByTestId("test-row")[1] as HTMLElement;
    expect(within(size).getByText("1/3")).toBeTruthy();
    expect(within(size).getByText("6.2 s")).toBeTruthy();
    expect(lastRuns([run("a", 1, { x: "pass" }), run("b", 2, { y: "fail" })]).get("x")).toEqual([
      "pass",
      null,
    ]);
  });

  it("suggests what it learned is worth a test, and hands it to the test writer", async () => {
    const { onAsk } = open();
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Checkout stops on an empty cart" }),
    );
    expect(onAsk).toHaveBeenCalledWith(
      "Write a test: Checkout stops on an empty cart. checkout/ has no test",
    );
    expect(screen.queryByText("Already there")).toBeNull();
    expect(
      suggestedAsk([
        { id: "a", title: "A", what: "", why: "", selected: false, exists: false },
        { id: "b", title: "B", what: "b", why: "", selected: false, exists: false },
      ]),
    ).toBe("Write these suggested tests:\n- A\n- B: b");
  });

  it("opens the composer from New test", () => {
    open();
    expect(screen.queryByRole("textbox", { name: "What should we test?" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New test" }));
    expect(screen.getByRole("textbox", { name: "What should we test?" })).toBeTruthy();
  });
});
