// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TargetDetailSchema } from "../../lib/schema";
import { PlaysThrough } from "./PlaysThrough";

afterEach(cleanup);

const target = (page: unknown) =>
  TargetDetailSchema.parse({ name: "relay-lite", adapter: "http", page });

describe("Plays through", () => {
  it("says a target plays through its API when it has no page map", () => {
    render(<PlaysThrough page={target(undefined).page} />);
    expect(screen.getByTestId("plays-through").textContent).toBe("Plays through: Its API");
  });

  it("says a target plays through its page, with its map in plain words, read only", () => {
    const page = target({
      map: [
        { what: "Message box", where: "textbox named “Message”" },
        { what: "Send", where: "button named “Send”" },
        { what: "Busy while", where: "button named “Stop”" },
      ],
    }).page;
    render(<PlaysThrough page={page} />);
    expect(screen.getByText("Its page")).toBeTruthy();
    expect(screen.getByText(/Set in the target's page: block/)).toBeTruthy();
    expect(
      screen.getByText(
        "Message box: textbox named “Message”; Send: button named “Send”; Busy while: button named “Stop”",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("says a target reached through its page alone plays through its page only, reply only", () => {
    const page = target({
      only: true,
      map: [{ what: "Done when", where: "nothing is busy" }],
    }).page;
    render(<PlaysThrough page={page} />);
    expect(screen.getByText("Its page only")).toBeTruthy();
    expect(screen.getByText(/a pass is reply only/)).toBeTruthy();
    expect(screen.getByText("Done when: nothing is busy")).toBeTruthy();
  });

  it("says why a page map doesn't read", () => {
    render(<PlaysThrough page={target({ error: "targets.x.page.url: must start" }).page} />);
    expect(screen.getByText(/Its page map doesn't read: targets.x.page.url/)).toBeTruthy();
  });
});
