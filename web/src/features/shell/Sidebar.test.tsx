// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Place } from "../../lib/router";
import { TargetSchema } from "../../lib/schema";
import { Sidebar } from "./Sidebar";

afterEach(cleanup);

const PLACE: Place = {
  target: "cartly",
  set: null,
  view: "home",
  run: null,
  case: null,
  base: null,
};

describe("the sidebar's order", () => {
  it("lists the test screens, then the app's own under its name, and no Ask AI item", () => {
    render(
      <Sidebar
        place={PLACE}
        targets={[TargetSchema.parse({ name: "cartly" })]}
        target="cartly"
        sets={[]}
        set={null}
        tests={7}
        failing={1}
        cards={null}
        onTarget={() => {}}
        onSet={() => {}}
        onView={() => {}}
      />,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    const labels = [...nav.querySelectorAll("button")].map((button) => button.textContent);
    expect(labels).toEqual([
      "Overview",
      "Tests7",
      "Runs1 failing",
      "Benchmarks",
      "What it learned",
      "Test environment",
    ]);
    expect(nav.querySelector("span.truncate.text-ink-3")?.textContent).toBe("cartly");
    expect(screen.getByRole("button", { name: "Settings" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Ask AI/ })).toBeNull();
  });
});
