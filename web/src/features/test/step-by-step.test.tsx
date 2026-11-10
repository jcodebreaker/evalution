// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { CaseFileViewSchema, TryResultSchema } from "../../lib/schema";
import { clientList, oneList, rowsOf } from "./one-list";
import { TestView } from "./TestView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const must = (id: string, text: string, actual: Record<string, unknown>, judged = "exact") => ({
  id,
  text,
  mode: "must",
  judged,
  actual,
});

/** The mock-up's "Remembers a shoe size", as an older server sends it: no `steps_timeline`. */
const shoe = (fields: Record<string, unknown> = {}) =>
  TryResultSchema.parse({
    name: "Remembers a shoe size",
    run: "r14",
    verdict: "flaky",
    try: 1,
    outcome: "failed",
    tries: [
      { n: 1, outcome: "failed", reason: null },
      { n: 2, outcome: "passed", reason: null },
    ],
    steps: [
      { kind: "before", title: "A fresh person" },
      {
        kind: "say",
        message: "I wear a size 43, keep that in mind.",
        actual: { passed: true, text: "Sent", reply: "Noted! I'll remember you wear a 43." },
        page: {
          shown: "Noted!",
          shown_at: 0.3,
          shot: "turn-1.png",
          shot_url: "/files/turn-1.png",
          check: { outcome: "passed" },
        },
      },
      {
        kind: "agent",
        items: [
          {
            id: "never-0",
            text: "Never adds a note",
            mode: "never",
            judged: "exact",
            actual: { passed: false, calls: ["add_note"], spans: ["s1"] },
          },
        ],
      },
      {
        kind: "state",
        items: [must("state-0", "Profile holds 43", { passed: false, text: "Profile is empty" })],
      },
      { kind: "break", title: "New conversation, same person" },
      {
        kind: "say",
        message: "Find me running shoes.",
        actual: { passed: true, text: "Sent", reply: "Here are four. What size do you wear?" },
      },
      {
        kind: "agent",
        items: [
          must("tool-1", "Searches in size 43", {
            passed: false,
            calls: ["search"],
            spans: ["s2"],
          }),
        ],
      },
      {
        kind: "reply",
        items: [
          must(
            "rubric-0",
            "Doesn't ask the size again",
            { passed: false, text: "It asks again" },
            "graded",
          ),
        ],
      },
    ],
    tool_rows: [
      {
        expected: "save_profile(size=43)",
        actual: null,
        status: "missing",
        reason: "not called",
        span: null,
      },
      {
        expected: "add_note",
        actual: "add_note(text='size 43')",
        status: "never",
        reason: "never to be called",
        span: "s1",
      },
      {
        expected: "crm__search(size=43)",
        actual: "crm__search(q='running shoes')",
        status: "wrong_args",
        reason: "size: 43 against nothing",
        span: "s2",
      },
      {
        expected: null,
        actual: "lookup()",
        status: "extra",
        reason: "a delegated agent's own call; not counted",
        span: "s3",
      },
    ],
    trajectory: {
      expected: ["Save profile", "Search"],
      tries: [{ n: 1, outcome: "failed", steps: [{ label: "Add note", status: "wrong" }] }],
    },
    detail: {
      timeline: [
        { id: "s1", parent: null, kind: "tool", name: "add_note", start: 1.4, seconds: 0.3 },
        { id: "s2", parent: null, kind: "tool", name: "crm__search", start: 3.6, seconds: 1 },
        {
          id: "s3",
          parent: null,
          kind: "tool",
          name: "lookup",
          start: 3.9,
          seconds: 0.2,
          helper: true,
        },
      ],
    },
    ...fields,
  });

/** The same try as a newer server sends it, its one list built there. */
const SERVER = [
  { kind: "message", status: null, span: null, at: 0, actor: "person", turn: 1, text: "Hi" },
  {
    kind: "call",
    status: "failed",
    span: "s1",
    at: 1.4,
    actor: "main",
    step: "never",
    expected: "add_note",
    made: "add_note(text='size 43')",
    reason: "never to be called",
  },
  {
    kind: "reply",
    status: "failed",
    span: null,
    at: 1.9,
    actor: "main",
    turn: 1,
    text: "Noted!",
    lines: [
      {
        id: "rubric:0",
        text: "Doesn't ask again",
        status: "failed",
        by: "model",
        role: "must",
        reason: null,
      },
    ],
  },
  {
    kind: "frame",
    status: "passed",
    span: null,
    at: 1.9,
    actor: "page",
    turn: 1,
    shown_at: 0.3,
    text: "Noted!",
    shot: "turn-1.png",
  },
  { kind: "health", status: "passed", span: null, at: null, actor: "page", why: "clean" },
];

describe("Step by step, built here from an older server's try", () => {
  it("lays the try out once, in time order: messages, calls, replies, state, breaks", () => {
    const entries = clientList(shoe());
    expect(entries.map((entry) => entry.kind)).toEqual([
      "before",
      "message",
      "call",
      "call",
      "reply",
      "frame",
      "state",
      "break",
      "message",
      "call",
      "call",
      "reply",
    ]);
    const calls = entries.filter((entry) => entry.kind === "call");
    expect(calls.map((call) => [call.step, call.status, call.at])).toEqual([
      ["missing", "failed", null],
      ["never", "failed", 1.4],
      ["wrong_args", "failed", 3.6],
      ["extra", null, 3.9],
    ]);
    // A delegated agent's own call decides nothing and says whose it is.
    expect(calls[3]?.actor).toBe("helper");
    const replies = entries.filter((entry) => entry.kind === "reply");
    expect(replies.map((reply) => reply.lines.map((line) => line.id))).toEqual([[], ["rubric-0"]]);
    expect(replies[1]?.status).toBe("failed");
    expect(entries.find((entry) => entry.kind === "frame")?.shot_url).toBe("/files/turn-1.png");
  });

  it("joins a reply and its frame, and calls that came together, into one row each", () => {
    const rows = rowsOf(clientList(shoe()));
    expect(rows.map((row) => `${row.kind}:${row.entries.length}`)).toEqual([
      "before:1",
      "message:1",
      "calls:2",
      "reply:2",
      "state:1",
      "break:1",
      "message:1",
      "calls:2",
      "reply:1",
    ]);
    expect(rows[2]?.status).toBe("failed");
  });
});

describe("Step by step, from the server's one list", () => {
  it("takes the server's list when the try carries it, with each frame's screenshot", () => {
    const { entries, from } = oneList(shoe({ steps_timeline: SERVER }));
    expect(from).toBe("server");
    expect(entries.map((entry) => entry.kind)).toEqual([
      "message",
      "call",
      "reply",
      "frame",
      "health",
    ]);
    expect(entries[3]?.shot_url).toBe("/files/turn-1.png");
    expect(oneList(shoe()).from).toBe("client");
  });
});

describe("A try's result", () => {
  const file = CaseFileViewSchema.parse({
    case: "shoe",
    name: "Remembers a shoe size",
    history: [{ run: "r14", at: 1794155180, verdict: "fail" }],
  });

  function open(result = shoe(), finding: unknown = null, onRunResult = vi.fn()) {
    vi.spyOn(api, "testPlan").mockResolvedValue(result);
    vi.spyOn(api, "tryResult").mockResolvedValue(result);
    vi.spyOn(api, "finding").mockResolvedValue(finding as never);
    render(
      <TestView
        scope={{ target: "cartly", set: "main" }}
        appName="cartly"
        id="shoe"
        file={{ status: "ready", data: file }}
        run="r14"
        live={false}
        runLabel={(run) => `Run ${run.slice(1)}`}
        stale={null}
        going={false}
        onRun={vi.fn()}
        onRunResult={onRunResult}
        onAsk={vi.fn()}
        onEdited={vi.fn()}
      />,
    );
  }

  const current = () =>
    screen
      .getAllByTestId("step-row")
      .findIndex((row) => row.getAttribute("aria-current") === "true");

  it("shows the alignment once: each call in one list, not again as lanes or rows", async () => {
    open();
    const list = await screen.findByRole("region", { name: "Step by step" });
    expect(list.getAttribute("data-from")).toBe("client");
    expect(within(list).getAllByTestId("call")).toHaveLength(4);
    expect(screen.queryByText("Trajectory")).toBeNull();
    expect(screen.queryByRole("region", { name: "Steps" })).toBeNull();
    // The first failed step is inspected first, beside the list.
    expect(current()).toBe(2);
    expect(
      within(screen.getByRole("region", { name: "Inspector" })).getByText(
        "Step 3 · What cartly did",
      ),
    ).toBeTruthy();
    // A server prefix stays apart from the tool's name.
    expect(within(list).getAllByTitle("crm__search(size=43)")[0]?.textContent).toBe(
      "crm·search(size=43)",
    );
  });

  it("draws the server's list when the try carries one", async () => {
    open(shoe({ steps_timeline: SERVER }));
    const list = await screen.findByRole("region", { name: "Step by step" });
    expect(list.getAttribute("data-from")).toBe("server");
    expect(within(list).getAllByTestId("step-row")).toHaveLength(4);
    expect(within(list).getByText("Shown at 0.3 s")).toBeTruthy();
  });

  it("times a picked step in the inspector as its row does: seconds from the try's start", async () => {
    open(shoe({ steps_timeline: SERVER }));
    const list = await screen.findByRole("region", { name: "Step by step" });
    const rows = within(list).getAllByTestId("step-row");
    for (const [index, at] of [
      [1, "1.4 s"],
      [2, "1.9 s"],
    ] as const) {
      fireEvent.click(rows[index] as HTMLElement);
      expect(within(rows[index] as HTMLElement).getByText(at)).toBeTruthy();
      const inspector = screen.getByRole("region", { name: "Inspector" });
      expect(inspector.querySelector("h2")?.parentElement?.textContent).toContain(at);
    }
  });

  it("jumps from an evidence chip to its step", async () => {
    open(shoe(), {
      summary: { text: "It searched without the size", refs: ["span:s2"], verified: true },
      cause: { text: "The size never reached the profile", refs: [], verified: true },
      fix: "Save sizes to the profile",
      suspected: "app",
    });
    const chip = await screen.findByRole("button", { name: "crm__search · 3.6 s" });
    expect(screen.getByText("App?")).toBeTruthy();
    fireEvent.click(chip);
    await waitFor(() => expect(current()).toBe(7));
  });

  it("jumps to where it first differs from a passing try, and opens that try", async () => {
    const onRunResult = vi.fn();
    open(
      shoe({
        divergence: {
          pass: { run: "r13", n: 2, span: "p2" },
          fail: { run: "r14", n: 1, span: "s2" },
          step: 1,
          kind: "args",
          detail: "size: 43 against nothing",
        },
      }),
      null,
      onRunResult,
    );
    const button = await screen.findByRole("button", {
      name: "Differs from passing try 2 of Run 13 at step 8",
    });
    fireEvent.click(button);
    await waitFor(() => expect(current()).toBe(7));
    expect(screen.getByText("Differs from the passing try")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "See the passing try 2 of Run 13" }));
    expect(onRunResult).toHaveBeenCalledWith("r13");
  });

  it("opens the trace, what the model saw and how it was decided as the workbench's other views", async () => {
    open();
    await screen.findByRole("region", { name: "Step by step" });
    const bench = screen.getByRole("group", { name: "Workbench" });
    fireEvent.click(within(bench).getByRole("button", { name: "Trace" }));
    expect(screen.getByRole("region", { name: "Trace" })).toBeTruthy();
    expect(screen.getByText("Each try's route")).toBeTruthy();
    expect(
      (within(bench).getByRole("button", { name: "What the model saw" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});
