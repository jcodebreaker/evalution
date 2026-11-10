// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PageHeader } from "./PageHeader";
import { RunGrid } from "./RunGrid";

afterEach(cleanup);

it("heads the Overview once, its facts as chips, with New test and Run all", () => {
  const onWrite = vi.fn();
  render(
    <PageHeader
      target="cartly"
      lastRunAt={Date.now() / 1000 - 120}
      chips={[
        { id: "how", text: "Plays on evalution's copy, reset before every try" },
        { id: "code", text: "main @ a41c9e2", tone: "mono" },
      ]}
      runAll={<button type="button">Run all</button>}
      onWrite={onWrite}
    />,
  );
  expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual([
    "How cartly is doing",
  ]);
  const chips = within(screen.getByTestId("header-chips"));
  expect(chips.getByText("main @ a41c9e2")).toBeTruthy();
  expect(chips.getByText("Last run 2 minutes ago")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "New test" }));
  expect(onWrite).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Run all" })).toBeTruthy();
});

it("leads from each test run by run to the Runs screen", () => {
  const onRuns = vi.fn();
  render(
    <RunGrid
      grid={{
        columns: [{ run: "r1", n: 1, latest: true }],
        rows: [
          {
            case: "a",
            name: "A",
            cells: [{ run: "r1", case: "a", state: "pass", title: "Run 1: passed" }],
          },
        ],
      }}
      setName={null}
      onTest={vi.fn()}
      onRun={vi.fn()}
      onRuns={onRuns}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "All runs →" }));
  expect(onRuns).toHaveBeenCalled();
});
