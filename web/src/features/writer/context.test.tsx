// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Place } from "../../lib/router";
import type { Agent } from "./agent";
import { askedText, screenContext, withContext } from "./context";
import { WriterPanel } from "./WriterPanel";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const at = (next: Partial<Place>): Place => ({
  target: "cartly",
  set: null,
  view: "home",
  run: null,
  case: null,
  base: null,
  ...next,
});
const names = {
  testName: (id: string) => (id === "shoe" ? "Remembers a shoe size" : id),
  runLabel: (run: string) => (run === "r14" ? "Run 14" : run),
  testRun: null as string | null,
  benchmarkRun: () => false,
};

describe("what is on screen", () => {
  it("names the open test and the run of its tries, and carries that run", () => {
    const context = screenContext(at({ view: "tests", case: "shoe" }), {
      ...names,
      testRun: "r14",
    });
    expect(context?.label).toBe("Remembers a shoe size · Run 14");
    expect(context?.run).toBe("r14");
    expect(context?.words).toContain('the test "Remembers a shoe size"');
  });

  it("names the open run, What it learned and Test environment", () => {
    expect(screenContext(at({ view: "runs", run: "r14" }), names)?.label).toBe("Run 14");
    expect(screenContext(at({ view: "runs", run: "r14" }), names)?.run).toBe("r14");
    expect(screenContext(at({ view: "app" }), names)?.label).toBe("What it learned");
    expect(screenContext(at({ view: "environment" }), names)?.label).toBe("Test environment");
    expect(screenContext(at({ view: "connect" }), names)).toBeNull();
  });

  it("goes before the question in plain words, and comes off again to show it", () => {
    const context = screenContext(at({ view: "app" }), names);
    const sent = withContext("Which tools last?", context);
    expect(sent).toBe("On screen: what evalution learned about cartly.\n\nWhich tools last?");
    expect(askedText(sent)).toEqual({
      asked: "Which tools last?",
      about: "what evalution learned about cartly",
    });
    expect(askedText("Hello")).toEqual({ asked: "Hello", about: null });
  });
});

function fakeAgent(ask: Agent["ask"]): Agent {
  const nothing = async () => {};
  return {
    session: null,
    queued: [],
    ask,
    approve: nothing,
    swapLine: nothing,
    adopt: () => {},
    refresh: nothing,
    stop: nothing,
    newChat: () => {},
    openChat: nothing,
  };
}

function panel(ask: Agent["ask"], context: ReturnType<typeof screenContext>, target = "cartly") {
  render(
    <WriterPanel
      target={target}
      agent={fakeAgent(ask)}
      runs={[]}
      runLabel={(run) => run}
      testName={(id) => id}
      going={false}
      onClose={() => {}}
      onRun={() => {}}
      onRunTest={() => {}}
      onTest={() => {}}
      onLearned={() => {}}
      draft=""
      onDrafted={() => {}}
      context={context}
    />,
  );
}

async function type(words: string) {
  fireEvent.change(screen.getByLabelText("Ask about your runs, traces or tests"), {
    target: { value: words },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("the assistant's composer", () => {
  it("sends the chip's context with the question, and its run", async () => {
    const ask = vi.fn(async () => {});
    panel(ask, screenContext(at({ view: "runs", run: "r14" }), names));
    expect(screen.getByText("Run 14")).toBeTruthy();
    await type("Why did it fail?");
    await waitFor(() => expect(ask).toHaveBeenCalled());
    expect(ask).toHaveBeenCalledWith("On screen: Run 14 (run r14).\n\nWhy did it fail?", "r14");
  });

  it("sends the question alone once the chip is removed", async () => {
    const ask = vi.fn(async () => {});
    panel(ask, screenContext(at({ view: "app" }), names));
    fireEvent.click(screen.getByRole("button", { name: "Remove context" }));
    expect(screen.queryByText("What it learned")).toBeNull();
    await type("Hello");
    await waitFor(() => expect(ask).toHaveBeenCalledWith("Hello", undefined));
  });

  it("opened to write a test, shows no chip", () => {
    panel(
      vi.fn(async () => {}),
      null,
    );
    expect(screen.queryByRole("button", { name: "Remove context" })).toBeNull();
  });
});

describe("what was typed and not sent", () => {
  const field = () =>
    screen.getByLabelText("Ask about your runs, traces or tests") as HTMLTextAreaElement;

  it("comes back when the panel opens again, for its own app only, and goes once sent", async () => {
    const ask = vi.fn(async () => {});
    panel(ask, null);
    fireEvent.change(field(), { target: { value: "Why is the second try slower" } });
    cleanup();
    panel(ask, null);
    expect(field().value).toBe("Why is the second try slower");
    cleanup();
    panel(ask, null, "other");
    expect(field().value).toBe("");
    cleanup();
    panel(ask, null);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(ask).toHaveBeenCalled());
    await waitFor(() => expect(field().value).toBe(""));
    cleanup();
    panel(ask, null);
    expect(field().value).toBe("");
  });

  it("is kept in the panel alone when the browser's storage can't be used", () => {
    const failing = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    panel(
      vi.fn(async () => {}),
      null,
    );
    fireEvent.change(field(), { target: { value: "Still here" } });
    expect(field().value).toBe("Still here");
    expect(failing).toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
