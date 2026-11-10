// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import {
  AgentMessageSchema,
  CaseFileViewSchema,
  ExplainSchema,
  TryResultSchema,
} from "../../lib/schema";
import { unjudgedWords } from "../../lib/verdict";
import { oneList, rowsOf } from "./one-list";
import { TestView } from "./TestView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const page = (n: number) => ({
  shown: "Done",
  shown_at: n,
  shot: `turn-${n}.png`,
  shot_url: `/files/turn-${n}.png`,
  check: { outcome: "passed" },
});

const row = (fields: Record<string, unknown>) => ({ guide: false, ...fields });

/** A page try of two turns with three UI checks: one element found, one not, one look unjudged. */
const liked = (fields: Record<string, unknown> = {}) =>
  TryResultSchema.parse({
    name: "Shows thumbs under a reply",
    run: "r3",
    verdict: "fail",
    try: 1,
    outcome: "failed",
    tries: [{ n: 1, outcome: "failed", reason: null }],
    steps: [
      { kind: "say", message: "Hi", actual: { passed: true, reply: "Hello" }, page: page(1) },
      {
        kind: "say",
        message: "Thanks",
        actual: { passed: true, reply: "Any time" },
        page: page(2),
      },
    ],
    explain: {
      code: {
        held: 1,
        total: 2,
        checks: [
          row({
            id: "element:shows:0",
            kind: "page_element",
            text: "found 1 button 'Copy' under turn 1's reply",
            outcome: "passed",
            turn: 1,
            evidence: [{ ref: "shot:turn-1.png" }],
          }),
          row({
            id: "element:shows:1",
            kind: "page_element",
            text: "no button named 'Thumbs up' under turn 2's reply; nearest: button 'Copy'",
            outcome: "failed",
            evidence: [{ ref: "page:2" }, { ref: "shot:turn-2.png" }],
          }),
        ],
      },
      model: {
        held: 0,
        total: 0,
        unjudged: 1,
        summary: null,
        reference: null,
        cut: 0,
        checks: [
          row({
            id: "look:0",
            kind: "page_look",
            text: "the grader model can't read images",
            outcome: "unjudged",
            unjudged: "no_vision",
            turn: 2,
          }),
        ],
      },
      judge: null,
      rules: 4,
      rules_from: null,
    },
    ...fields,
  });

describe("A try's UI checks, step by step", () => {
  it("puts each in its turn's step, with what the page showed of the reply", () => {
    const rows = rowsOf(oneList(liked()).entries);
    expect(
      rows.map((found) => `${found.kind}:${found.entries.map((e) => e.kind).join("+")}`),
    ).toEqual([
      "message:message",
      "reply:reply+frame+page_element",
      "message:message",
      "reply:reply+frame+page_element+page_look",
    ]);
    expect(rows[3]?.status).toBe("failed");
    const failed = rows[3]?.entries[2];
    expect([failed?.turn, failed?.shot_url]).toEqual([2, "/files/turn-2.png"]);
  });

  it("takes them from how the try was decided, beside the server's own list", () => {
    const shown = liked({
      steps_timeline: [
        { kind: "message", turn: 1, text: "Hi" },
        { kind: "reply", turn: 1, text: "Hello", status: "passed" },
        { kind: "message", turn: 2, text: "Thanks" },
        { kind: "reply", turn: 2, text: "Any time", status: "passed" },
      ],
    });
    const entries = oneList(shown).entries;
    expect(entries.map((entry) => entry.kind)).toEqual([
      "message",
      "reply",
      "page_element",
      "message",
      "reply",
      "page_element",
      "page_look",
    ]);
    expect(entries[2]?.shot_url).toBe("/files/turn-1.png");
    expect(entries[6]?.unjudged).toBe("no_vision");
  });
});

describe("A try's result with UI checks", () => {
  const file = CaseFileViewSchema.parse({ case: "liked", name: "Shows thumbs under a reply" });

  function open(result = liked()) {
    vi.spyOn(api, "testPlan").mockResolvedValue(result);
    vi.spyOn(api, "tryResult").mockResolvedValue(result);
    vi.spyOn(api, "finding").mockResolvedValue(null as never);
    render(
      <TestView
        scope={{ target: "chat", set: "main" }}
        appName="chat"
        id="liked"
        file={{ status: "ready", data: file }}
        run="r3"
        live={false}
        runLabel={(run) => run}
        stale={null}
        going={false}
        onRun={vi.fn()}
        onRunResult={vi.fn()}
        onAsk={vi.fn()}
        onEdited={vi.fn()}
      />,
    );
  }

  it("draws each with its mark and why; a look unjudged says why in plain words", async () => {
    open();
    const list = await screen.findByRole("region", { name: "Step by step" });
    const results = within(list).getAllByTestId("ui-result");
    expect(results.map((found) => found.getAttribute("data-status"))).toEqual([
      "passed",
      "failed",
      "unjudged",
    ]);
    expect(within(results[0] as HTMLElement).getByLabelText("Held")).toBeTruthy();
    expect(
      within(results[1] as HTMLElement).getByText(
        "no button named 'Thumbs up' under turn 2's reply; nearest: button 'Copy'",
      ),
    ).toBeTruthy();
    expect(
      within(results[2] as HTMLElement).getByText(
        "Couldn't judge: the grader model can't read images",
      ),
    ).toBeTruthy();
    expect(within(results[2] as HTMLElement).getByText("model")).toBeTruthy();
  });

  it("shows the turn's screenshot, enlarged, beside the check picked", async () => {
    open();
    const list = await screen.findByRole("region", { name: "Step by step" });
    const failed = within(list).getAllByTestId("ui-result")[1] as HTMLElement;
    fireEvent.click(within(failed).getByRole("button"));
    const inspector = screen.getByRole("region", { name: "Inspector" });
    const shot = within(inspector).getByTestId("ui-shot");
    expect(shot.getAttribute("src")).toBe("/files/turn-2.png");
    expect(within(inspector).getByTestId("ui-facts").textContent).toContain(
      "nearest: button 'Copy'",
    );
    expect(within(failed).getByRole("button").getAttribute("aria-pressed")).toBe("true");

    // The first turn's check shows the first turn's screenshot.
    const held = within(list).getAllByTestId("ui-result")[0] as HTMLElement;
    fireEvent.click(within(held).getByRole("button"));
    expect(
      within(screen.getByRole("region", { name: "Inspector" }))
        .getByTestId("ui-shot")
        .getAttribute("src"),
    ).toBe("/files/turn-1.png");
  });

  it("lets a person agree or not with the model on a look it judged", async () => {
    const shown = liked();
    const look = shown.explain?.model.checks?.[0];
    if (look) Object.assign(look, { outcome: "passed", unjudged: null, text: "A thumbs-up shows" });
    open(
      TryResultSchema.parse({
        ...shown,
        agreement: { "look:0": { agreed: 0, labelled: 0, unreliable: false, can: true } },
      }),
    );
    const list = await screen.findByRole("region", { name: "Step by step" });
    const row = within(list).getAllByTestId("ui-result")[2] as HTMLElement;
    expect(within(row).getByRole("button", { name: "Agree" })).toBeTruthy();
    const label = vi.spyOn(api, "label").mockResolvedValue({} as never);
    fireEvent.click(within(row).getByRole("button", { name: "Disagree" }));
    expect(label).toHaveBeenCalledWith("r3", "liked", 1, "look:0", false);
  });
});

describe("Why a UI check wasn't judged", () => {
  it("says the server's reasons, and a broken page map apart from one not learned yet", () => {
    expect(unjudgedWords(["no_page_map"])).toBe("the page hasn't been learned yet");
    expect(unjudgedWords(["page_map"])).toBe("the page lacks what its page map names");
    expect(unjudgedWords(["no_vision"])).toBe("the grader model can't read images");
    expect(unjudgedWords(["no_screenshot"])).toBe("no screenshot of the turn was kept");
  });
});

describe("Older results and messages", () => {
  it("read without any UI check field", () => {
    const explain = ExplainSchema.parse({
      code: {
        held: 1,
        total: 1,
        checks: [{ id: "shown:1", kind: "shown", text: "showed", outcome: "passed", guide: true }],
      },
      model: { held: 0, total: 0, unjudged: 0, summary: null, reference: null, cut: 0 },
      judge: null,
      rules: 3,
      rules_from: null,
    });
    expect(explain.model.checks).toBeUndefined();
    expect(explain.code.checks[0]?.why).toBeUndefined();
    const file = CaseFileViewSchema.parse({ name: "Old", page: { decides: true } });
    expect(file.page).toEqual({ shows: [], hides: [], looks: [] });
    expect(CaseFileViewSchema.parse({ name: "Older" }).page).toBeNull();
    const message = AgentMessageSchema.parse({
      id: "m",
      role: "agent",
      at: 0,
      text: "",
      plan: { steps: [], tries: 3, case: "x" },
    });
    expect([message.plan?.page, message.page_offer]).toEqual([undefined, undefined]);
    const shown = liked({ explain: undefined });
    expect(rowsOf(oneList(shown).entries).map((found) => found.kind)).toEqual([
      "message",
      "reply",
      "message",
      "reply",
    ]);
  });
});
