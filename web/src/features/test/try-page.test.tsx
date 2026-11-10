// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { CaseFileViewSchema, CompareSchema, TryResultSchema } from "../../lib/schema";
import { clock, PageFilm } from "./PageFilm";
import { Steps } from "./Steps";
import { TestView } from "./TestView";
import { Turns } from "./Turns";
import { breakCells, tryFacts, turnShown } from "./try-words";
import { VerdictBanner } from "./VerdictBanner";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SHOT = "/v1/evals/runs/20261108-162620-839b/cases/code-block/tries/1/files/turn-1.png";

/** Run 20261108-162620-839b's "A code block shows", as the server sent it: the page's message
 * got 503 and the turn ran out of time with no reply. */
const refused = (fields: Record<string, unknown> = {}) =>
  CompareSchema.parse({
    case: "code-block",
    name: "A code block shows",
    about: "What the page shows of a reply with formatting.",
    limits: ["1 try", "≤ 120 s each", "from page testing, ADR 0010"],
    steps: [
      { kind: "before", title: "Opens a fresh chat", actual: { passed: true, text: "Done" } },
      {
        kind: "say",
        title: "Asks for a code block",
        message:
          "Reply with only a Python code block that prints hello three times with a for loop.",
        actual: { passed: false, text: "the app didn't answer the page's message in time" },
        page: {
          shown: null,
          shot: "turn-1.png",
          shot_url: SHOT,
          check: {
            outcome: "unjudged",
            why: "turn 1 ended in an error, so there was no reply to show",
          },
        },
      },
      {
        kind: "passes",
        title: "The result holds",
        actual: {
          passed: false,
          text: "the app answered a request from the page with an error, 503",
        },
      },
    ],
    try: 1,
    outcome: "failed",
    label: "Failed check",
    called: false,
    reason: "the app answered a request from the page with an error, 503",
    seconds: 122.6,
    explain: {
      code: {
        held: 0,
        total: 3,
        checks: [
          {
            id: "turn:1",
            kind: "timeout",
            text: "timed out, and no sign of the agent working: the app didn't answer the page's message in time",
            outcome: "unjudged",
            guide: false,
          },
          {
            id: "shown:1",
            kind: "shown",
            text: "turn 1 ended in an error, so there was no reply to show",
            outcome: "unjudged",
            guide: false,
          },
          {
            id: "page_health",
            kind: "page_health",
            text: "1 console error(s), first 'Failed to load resource: …'; 1 failed request(s), first POST /v1/sessions/95834ea5/events: 503",
            outcome: "failed",
            guide: false,
          },
        ],
      },
      model: { held: 0, total: 0, unjudged: 0, summary: null, reference: null, cut: 0 },
      judge: null,
      rules: 3,
      rules_from: null,
    },
    page: {
      console: [
        {
          level: "error",
          text: "Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
          at: 163.275,
        },
      ],
      failed_requests: [
        { method: "POST", path: "/v1/sessions/95834ea5/events", status: 503, at: 163.274 },
      ],
      health: { outcome: "failed", why: "1 console error(s), …" },
      shown_alone: "unjudged",
      without_page: "unjudged",
      video: null,
      video_missing: "the browser didn't finish the try's video",
      interrupted: true,
    },
    run: "20261108-162620-839b",
    verdict: "fail",
    layers: { stored: "absent", answered: "unjudged", shown: "fail" },
    tries: [{ n: 1, outcome: "failed", reason: null, label: "Failed check" }],
    ...fields,
  });

const TABLE =
  "| Fruit  | Colour |\n|--------|--------|\n| Apple  | Red    |\n| Banana | Yellow |\n| Grape  | Purple |";

/** Run 20261108-162158-b581's "A Markdown table shows": it passed, the table showed at 36.3 s. */
const table = (fields: Record<string, unknown> = {}) =>
  CompareSchema.parse({
    name: "A Markdown table shows",
    run: "20261108-162158-b581",
    steps: [
      {
        kind: "say",
        title: "Asks for a fruit table",
        message: "Make a Markdown table with two columns, Fruit and Colour.",
        actual: { passed: true, text: "Sent", reply: TABLE },
        page: {
          shown: "Fruit\tColour\nApple\tRed\nBanana\tYellow\nGrape\tPurple",
          shown_at: 36.251,
          shot_url: "/files/table-1.png",
          check: { outcome: "passed", why: "turn 1's reply showed on the page" },
        },
      },
    ],
    try: 1,
    passed: true,
    outcome: "passed",
    label: "Passed",
    seconds: 34.4,
    page: {
      health: { outcome: "passed", why: "no console errors, and no failed requests to the copy" },
      shown_alone: "pass",
      without_page: "passed",
      video: { name: "try.webm", url: "/files/try.webm" },
    },
    layers: { stored: "absent", answered: "pass", shown: "pass" },
    tries: [{ n: 1, outcome: "passed", reason: null, label: "Passed" }],
    ...fields,
  });

const cells = () =>
  within(screen.getByRole("list", { name: "Which layer held" }))
    .getAllByRole("listitem")
    .map((item) => item.textContent);

describe("Where it broke", () => {
  it("says each layer of a page try in a line, the broken one on a red wash", () => {
    render(<VerdictBanner shown={refused()} runLabel={() => "Run 9"} onTry={() => null} />);
    expect(cells()).toEqual([
      "StoredNothing to check",
      "AnsweredNever answered the page's message",
      "ShownTurn 1 ended in an error, so there was no reply to show",
      "Page healthy1 console error, 1 failed request",
    ]);
    const broke = screen
      .getAllByRole("listitem")
      .filter((item) => item.getAttribute("data-broke") === "true");
    expect(broke.map((item) => item.getAttribute("title"))).toEqual(["Page healthy: failed"]);
    expect(broke[0]?.className).toContain("bg-fail-soft");
    // The one mark: a dot, never a tick or a cross.
    expect(screen.queryByText(/[✓✕]/)).toBeNull();
    expect(screen.getByRole("img", { name: "Stored: nothing to check" })).toBeTruthy();
  });

  it("says every layer held on a try that passed", () => {
    expect(breakCells(table())?.map((cell) => [cell.name, cell.state, cell.line])).toEqual([
      ["Stored", "none", "Nothing to check"],
      ["Answered", "pass", "The reply is right"],
      ["Shown", "pass", "Every reply showed"],
      ["Page healthy", "pass", "No errors, no failed requests"],
    ]);
  });

  it("says the turn whose reply the page showed only in part", () => {
    const shown = table({
      outcome: "failed",
      passed: false,
      layers: { stored: "absent", answered: "pass", shown: "fail" },
      page: { shown_alone: "fail", health: { outcome: "passed" }, without_page: "passed" },
      steps: [
        {
          kind: "say",
          message: "Make a table",
          actual: { passed: true, reply: TABLE },
          page: {
            shown: "Fruit Colour",
            check: { outcome: "failed" },
            part: { shown: "Fruit Colour", missing: "Apple Red" },
          },
        },
      ],
    });
    expect(breakCells(shown)?.find((cell) => cell.layer === "shown")?.line).toBe(
      "Turn 1's reply showed only in part",
    );
  });

  it("says only the layers there are for a try through the API", () => {
    const api = refused({
      page: null,
      layers: { stored: "fail", answered: "pass", shown: "absent" },
      explain: undefined,
    });
    expect(breakCells(api)?.map((cell) => [cell.name, cell.line])).toEqual([
      ["Stored", "What it stored is wrong"],
      ["Answered", "The reply is right"],
    ]);
    expect(breakCells({ ...api, layers: undefined })).toBeNull();
  });
});

describe("The try's facts", () => {
  it("says how long, how many messages and how it played, without icons", () => {
    expect(tryFacts(refused()).map((fact) => fact.text)).toEqual([
      "123 s",
      "1 message",
      "Through the page",
    ]);
    expect(tryFacts(refused({ seconds: 1.08, page: null })).map((fact) => fact.text)).toEqual([
      "1.1 s",
      "1 message",
      "Through the API",
    ]);
  });
});

describe("What the page showed", () => {
  it("lays each message's screenshot as a frame with its mark, and the video last", () => {
    render(<PageFilm shown={table()} />);
    const film = screen.getByTestId("page-film");
    const frames = within(film).getAllByRole("button", { pressed: false });
    expect(within(film).getByRole("button", { name: "Message 1: shown at 36.3 s" })).toBeTruthy();
    expect(within(film).getByRole("img", { name: "Shown at 36.3 s" })).toBeTruthy();
    expect(frames.map((frame) => frame.getAttribute("aria-label"))).toContain("Video of the try");
    // The message is shown large first; the video once picked.
    expect(screen.getByAltText("The page after message 1").getAttribute("src")).toBe(
      "/files/table-1.png",
    );
    fireEvent.click(screen.getByRole("button", { name: "Video of the try" }));
    expect(screen.getByLabelText("Recording of the try on the page").getAttribute("src")).toBe(
      "/files/try.webm",
    );
    expect(screen.queryByAltText("The page after message 1")).toBeNull();
  });

  it("says what showed of a turn with no reply", () => {
    render(<PageFilm shown={refused()} />);
    expect(screen.queryByRole("button", { name: "Video of the try" })).toBeNull();
    expect(screen.getByRole("button", { name: "Message 1: nothing to show" })).toBeTruthy();
    expect(clock(1.2)).toBe("0:01");
    expect(clock(125)).toBe("2:05");
  });
});

describe("Turn by turn", () => {
  it("lays what was said, answered and shown side by side, and marks the missing words", () => {
    const shown = table({
      steps: [
        {
          kind: "say",
          message: "Make a table",
          actual: { passed: true, reply: "Here it is: Apple Red" },
          page: {
            shown: "Here it is:",
            check: { outcome: "failed", why: "the page showed only part of it" },
            part: { shown: "Here it is:", missing: "Apple Red" },
          },
        },
      ],
    });
    render(<Turns shown={shown} />);
    const turn = screen.getByRole("listitem", { name: "Turn 1" });
    expect(within(turn).getByText("Shown only in part")).toBeTruthy();
    expect(turn.querySelector("mark")?.textContent).toBe("Apple Red");
    expect(within(turn).getByText("The person said")).toBeTruthy();
    expect(within(turn).getByText("The page showed")).toBeTruthy();
  });

  it("says when each reply showed, and nothing of a page for a try through the API", () => {
    render(<Turns shown={table()} />);
    expect(screen.getByText("Shown at 36.3 s")).toBeTruthy();
    cleanup();
    const api = table({ page: null, layers: null });
    render(<Turns shown={{ ...api, steps: api.steps.map((step) => ({ ...step, page: null })) }} />);
    const turn = screen.getByRole("listitem", { name: "Turn 1" });
    expect(within(turn).getByText("The app answered")).toBeTruthy();
    expect(within(turn).queryByText("The page showed")).toBeNull();
    expect(within(turn).getByText("Sent")).toBeTruthy();
    expect(turnShown(table().steps[0]?.page ?? ({} as never)).state).toBe("pass");
  });
});

describe("Steps", () => {
  it("puts each step on a line with its dot, and when the page showed the reply", () => {
    render(<Steps steps={table().steps} names={{}} appName="relay-lite" result />);
    expect(screen.getByRole("img", { name: "Held" })).toBeTruthy();
    expect(screen.getByText("36.3 s")).toBeTruthy();
    expect(screen.getByText("Step 1 · The person says")).toBeTruthy();
  });
});

describe("A test's page", () => {
  const file = CaseFileViewSchema.parse({
    case: "code-block",
    name: "A code block shows",
    about: "What the page shows of a reply with formatting.",
    plays: "page",
    history: [{ run: "20261108-162620-839b", at: 1794155180, verdict: "fail" }],
  });

  function open(onRun = vi.fn()) {
    vi.spyOn(api, "testPlan").mockResolvedValue(refused());
    vi.spyOn(api, "tryResult").mockResolvedValue(TryResultSchema.parse(refused()));
    vi.spyOn(api, "finding").mockRejectedValue(new Error("none"));
    render(
      <TestView
        scope={{ target: "relay-lite", set: "page-stress" }}
        appName="relay-lite"
        id="code-block"
        file={{ status: "ready", data: file }}
        run="20261108-162620-839b"
        live={false}
        runLabel={() => "Run 9"}
        stale={null}
        going={false}
        onRun={onRun}
        onRunResult={vi.fn()}
        onAsk={vi.fn()}
        onEdited={vi.fn()}
      />,
    );
    return { onRun };
  }

  it("heads the try once, with its facts and its actions, and no way back of its own", async () => {
    const { onRun } = open();
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Where it broke" })).toBeTruthy(),
    );
    // The shell's top bar holds the way back; the screen's title appears once.
    expect(screen.queryByRole("button", { name: /relay-lite · Run 9/ })).toBeNull();
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual([
      "A code block shows",
    ]);
    expect(
      within(screen.getByTestId("facts"))
        .getAllByText(/./)
        .map((chip) => chip.textContent),
    ).toEqual(["123 s", "1 message", "Through the page", "Run 9 · try 1 of 1"]);
    fireEvent.click(screen.getByRole("button", { name: "Run this test again" }));
    expect(onRun).toHaveBeenCalledWith("A code block shows");
    expect(screen.getByRole("button", { name: "Edit test" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "What the page showed" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Step by step" })).toBeTruthy();
    // The turns side by side are kept, under a fold.
    expect(screen.getByText("Each turn, side by side")).toBeTruthy();
  });

  it("switches to what it checks, and back to the latest result", async () => {
    open();
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Where it broke" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("tab", { name: "What it checks" }));
    expect(screen.getByTestId("how-it-plays")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Where it broke" })).toBeNull();
    expect(screen.getByText("Through the page")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Latest result" }));
    expect(screen.getByRole("region", { name: "Where it broke" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "History 1" }));
    expect(screen.getByRole("region", { name: "History" })).toBeTruthy();
  });
});
