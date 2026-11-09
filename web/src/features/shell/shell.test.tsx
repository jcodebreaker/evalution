// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Place } from "../../lib/router";
import { Button } from "../../ui/button";
import { VerdictGlyph } from "../shared/marks";
import { crumbsFor } from "./crumbs";
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
  target: "relay-lite",
  setName: "Page stress",
  runLabel: (id: string) => `Run ${id.slice(1)}`,
  testName: (id: string) => (id === "t1" ? "A code block shows" : id),
};

describe("the top bar's breadcrumbs", () => {
  it("names the app, its test set and the screen", () => {
    expect(crumbsFor(place({ view: "home" }), names)).toEqual([
      "relay-lite",
      "Page stress",
      "Overview",
    ]);
    expect(crumbsFor(place({ view: "tests" }), names)).toEqual([
      "relay-lite",
      "Page stress",
      "Tests",
    ]);
  });

  it("follows a run down to the try of one of its tests", () => {
    expect(crumbsFor(place({ view: "runs", run: "r9" }), names)).toEqual([
      "relay-lite",
      "Page stress",
      "Run 9",
    ]);
    expect(crumbsFor(place({ view: "runs", run: "r9", case: "t1" }), names)).toEqual([
      "relay-lite",
      "Page stress",
      "Run 9",
      "A code block shows",
    ]);
    expect(crumbsFor(place({ view: "tests", case: "t1" }), names)).toEqual([
      "relay-lite",
      "Page stress",
      "Tests",
      "A code block shows",
    ]);
  });

  it("leaves the test set out of the app's own screens, and the app out of Settings", () => {
    expect(crumbsFor(place({ view: "environment" }), names)).toEqual([
      "relay-lite",
      "Test environment",
    ]);
    expect(crumbsFor(place({ view: "model" }), names)).toEqual(["Settings"]);
    expect(crumbsFor(place({ view: "home" }), { ...names, target: null })).toEqual([
      "Connect an app",
    ]);
  });
});

describe("the top bar", () => {
  it("marks where the person is, and opens the run playing from its pill", () => {
    const onOpen = vi.fn();
    render(
      <TopBar
        crumbs={["relay-lite", "Page stress", "Overview"]}
        live={{ label: "Run 10 · 2 of 4 tests", onOpen }}
      />,
    );
    const where = screen.getByRole("navigation", { name: "Where you are" });
    expect(within(where).getByText("Overview").getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "Run 10 · 2 of 4 tests" }));
    expect(onOpen).toHaveBeenCalled();
  });

  it("shows no pill while nothing plays", () => {
    render(<TopBar crumbs={["relay-lite"]} live={null} />);
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
      asking={false}
      onAskAi={null}
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

  it("says the isolation plan is proven only when it is, and opens the Test environment", () => {
    const onView = vi.fn();
    sidebar({ plan: { status: "proven", version: 2 }, onView });
    fireEvent.click(screen.getByRole("button", { name: /Plan v2 proven/ }));
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
