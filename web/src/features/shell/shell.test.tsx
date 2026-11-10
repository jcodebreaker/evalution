// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Place } from "../../lib/router";
import { Button } from "../../ui/button";
import { VerdictGlyph } from "../shared/marks";
import { backFor } from "./crumbs";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

afterEach(cleanup);

const place = (next: Partial<Place>): Place => ({
  target: "relay-lite",
  set: null,
  view: "home",
  run: null,
  case: null,
  base: null,
  ...next,
});

const names = {
  runLabel: (id: string) => `Run ${id.slice(1)}`,
  benchmarkRun: (id: string) => id === "b1",
};

describe("the top bar's way back", () => {
  it("has none on a top-level screen: its own heading is its title", () => {
    for (const view of [
      "home",
      "tests",
      "runs",
      "benchmarks",
      "app",
      "environment",
      "model",
    ] as const) {
      expect(backFor(place({ view }), names)).toBeNull();
    }
  });

  it("goes from a test to Tests, or to the run it was opened from", () => {
    expect(backFor(place({ view: "tests", case: "t1" }), names)).toEqual({
      label: "Tests",
      list: "tests",
      to: {},
    });
    expect(backFor(place({ view: "runs", run: "r9", case: "t1" }), names)).toEqual({
      label: "Run 9",
      list: null,
      to: { case: null },
    });
  });

  it("goes from a run to Runs, and from a benchmark's try to Benchmarks", () => {
    expect(backFor(place({ view: "runs", run: "r9" }), names)?.label).toBe("Runs");
    expect(backFor(place({ view: "runs", run: "b1" }), names)).toBeNull();
    expect(backFor(place({ view: "runs", run: "b1", case: "q1" }), names)?.label).toBe(
      "Benchmarks",
    );
  });
});

describe("the top bar", () => {
  it("leads back, and opens the run playing from its pill", () => {
    const onOpen = vi.fn();
    const onBack = vi.fn();
    render(
      <TopBar
        back={{ label: "Tests", onClick: onBack }}
        live={{ label: "Run 10 · 2 of 4 tests", onOpen }}
      />,
    );
    expect(screen.queryByRole("navigation")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tests" }));
    expect(onBack).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Run 10 · 2 of 4 tests" }));
    expect(onOpen).toHaveBeenCalled();
  });

  it("shows no button on a top-level screen while nothing plays", () => {
    render(<TopBar back={null} live={null} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

const sidebar = (props: Partial<Parameters<typeof Sidebar>[0]>) =>
  render(
    <Sidebar
      place={place({ view: "home" })}
      targets={[]}
      target="relay-lite"
      sets={[]}
      set={null}
      cards={null}
      onTarget={() => {}}
      onSet={() => {}}
      onView={() => {}}
      {...props}
    />,
  );

describe("the sidebar", () => {
  it("counts the tests, and the failures of the last run in red, only when there are some", () => {
    sidebar({ tests: 4, failing: 3 });
    expect(screen.getByRole("button", { name: "Tests, 4" })).toBeTruthy();
    const failing = screen.getByText("3 failing");
    expect(failing.className).toContain("text-fail");
    cleanup();
    sidebar({ tests: 4, failing: 0 });
    expect(screen.queryByText(/failing/)).toBeNull();
    expect(screen.getByRole("button", { name: "Runs" })).toBeTruthy();
  });

  it("says tests run on a copy only when its plan is proven, and opens the Test environment", () => {
    const onView = vi.fn();
    sidebar({ plan: { status: "proven", version: 2 }, onView });
    const copy = screen.getByRole("button", { name: "Tests run on a copy" });
    expect(copy.title).toContain("Plan v2 proven");
    fireEvent.click(copy);
    expect(onView).toHaveBeenCalledWith("environment");
    cleanup();
    sidebar({ plan: { status: "stale", version: 2 } });
    expect(screen.queryByText(/proven/)).toBeNull();
  });

  it("names the test set as words when there is only one to pick", () => {
    sidebar({ sets: [{ set: "stress", name: "Page stress" }] as never, set: "stress" });
    expect(screen.getByText("Page stress").tagName).toBe("B");
    expect(screen.queryByRole("button", { name: "Test set" })).toBeNull();
  });
});

describe("an outcome", () => {
  it("is a dot that says it, never a tick or a cross", () => {
    render(<VerdictGlyph verdict="fail" />);
    const dot = screen.getByRole("img", { name: "Failed" });
    expect(dot.tagName).toBe("SPAN");
    expect(dot.className).toContain("bg-fail");
    expect(document.querySelector("svg")).toBeNull();
  });
});

describe("the buttons", () => {
  it("make the primary solid purple, the rest a 1px line, none with a shadow", () => {
    render(
      <>
        <Button>Run all</Button>
        <Button variant="secondary">Write a test</Button>
      </>,
    );
    const primary = screen.getByRole("button", { name: "Run all" }).className;
    const secondary = screen.getByRole("button", { name: "Write a test" }).className;
    expect(primary).toContain("bg-tint");
    expect(primary).toContain("h-8");
    expect(primary).toContain("rounded-[6px]");
    expect(secondary).toContain("border-rule-strong");
    expect(`${primary} ${secondary}`).not.toContain("shadow");
  });
});
