// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CompareSchema, PlanStepSchema, StepPageSchema, TryPageSchema } from "../../lib/schema";
import { API_ALONE, layerWords, PAGE_ONLY, shownWords } from "../../lib/verdict";
import { pageLines } from "./Page";
import { Steps } from "./Steps";
import { VerdictBanner } from "./VerdictBanner";

afterEach(cleanup);

function banner(fields: Record<string, unknown>) {
  const shown = CompareSchema.parse({
    name: "Remembers the dog",
    run: "r14",
    try: 3,
    tries: [
      { n: 1, outcome: "passed", reason: null },
      { n: 3, outcome: "failed", reason: "turn 2's reply never showed on the page" },
    ],
    ...fields,
  });
  render(<VerdictBanner shown={shown} runLabel={() => "Run 14"} onTry={() => null} />);
}

const pills = () =>
  within(screen.getByRole("list", { name: "Which layer held" }))
    .getAllByRole("listitem")
    .map((item) => item.getAttribute("title"));

describe("The layered banner", () => {
  it("says every layer held on a page try that passed", () => {
    banner({
      outcome: "passed",
      passed: true,
      label: "Passed",
      layers: { stored: "pass", answered: "pass", shown: "pass" },
      page: { without_page: "passed" },
    });
    expect(screen.getByText(/^Passed on try 3/)).toBeTruthy();
    expect(pills()).toEqual(["Stored: held", "Answered: held", "Shown: held"]);
    expect(screen.queryByText(new RegExp(API_ALONE))).toBeNull();
  });

  it("says a try failed on the page, not in memory, and that the API alone would have passed", () => {
    banner({
      outcome: "failed",
      reason: "turn 2's reply never showed on the page",
      layers: { stored: "pass", answered: "pass", shown: "fail" },
      page: { without_page: "passed" },
    });
    expect(screen.getByText("Failed on the page, not in memory · try 3")).toBeTruthy();
    expect(screen.getByText(new RegExp(API_ALONE.replace(".", "\\.")))).toBeTruthy();
    expect(pills()).toEqual(["Stored: held", "Answered: held", "Shown: failed"]);
  });

  it("doesn't say the API alone would have passed when what was stored failed too", () => {
    banner({
      outcome: "failed",
      label: "Failed",
      layers: { stored: "fail", answered: "pass", shown: "fail" },
      page: { without_page: "failed" },
    });
    expect(screen.getByText(/^Failed on try 3/)).toBeTruthy();
    expect(screen.queryByText(/Failed on the page/)).toBeNull();
    expect(screen.queryByText(new RegExp(API_ALONE))).toBeNull();
    expect(pills()).toEqual(["Stored: failed", "Answered: held", "Shown: failed"]);
  });

  it("keeps the banner as it was for a try through the API", () => {
    banner({
      outcome: "failed",
      label: "Failed",
      layers: { stored: "fail", answered: "pass", shown: "absent" },
    });
    expect(screen.getByText(/^Failed on try 3/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Which layer held" })).toBeNull();
    // A server from before layers says nothing of them either.
    expect(layerWords(undefined, "failed")).toBeNull();
  });

  it("only says so of the API when every check but the page's held", () => {
    const layers = { stored: "absent", answered: "pass", shown: "fail" } as const;
    expect(layerWords(layers, "failed", "passed")?.line).toBe(API_ALONE);
    // A rubric line failed besides: the API alone would have failed it too.
    expect(layerWords(layers, "failed", "failed")?.line).toBeNull();
    expect(layerWords(layers, "failed", "failed")?.headline).toBe(
      "Failed on the page, not in the answer",
    );
  });

  it("blames the app, not the page, for a message it refused or never took", () => {
    banner({
      outcome: "unjudged",
      label: "App busy",
      reason: "The app, or a gateway in front of it, was busy or down",
      layers: { stored: "absent", answered: "unjudged", shown: "unjudged" },
      page: { without_page: "unjudged", not_taken: { status: 503 } },
    });
    expect(screen.getByText("The app refused the message (503) · try 3")).toBeTruthy();
    expect(screen.queryByText(/Failed on the page/)).toBeNull();
    expect(pills()).toEqual(["Answered: couldn't judge", "Shown: couldn't judge"]);
    // Even where only the page's own checks failed, a message the app didn't take is the app's.
    const layers = { stored: "absent", answered: "absent", shown: "fail" } as const;
    expect(layerWords(layers, "failed", "passed", false, { status: null })?.headline).toBe(
      "The app didn't take the message",
    );
    expect(layerWords(layers, "failed", "passed")?.headline).toBe("Failed on the page");
  });

  it("says a pass through the page alone is reply only, and never speaks of an API", () => {
    banner({
      outcome: "passed",
      passed: true,
      label: "Passed",
      layers: { stored: "absent", answered: "pass", shown: "pass" },
      page: { without_page: null, page_only: true },
    });
    expect(screen.getByText(`Passed on try 3, ${PAGE_ONLY}`)).toBeTruthy();
    const layers = { stored: "absent", answered: "pass", shown: "fail" } as const;
    expect(layerWords(layers, "failed", null, true)?.line).toBeNull();
    expect(layerWords(layers, "failed", null, true)?.apiAlone).toBe(false);
    expect(layerWords(layers, "failed", null, true)?.headline).toBe(
      "Failed on the page, not in the answer",
    );
  });
});

describe("A message's step on the page", () => {
  const page = (fields: Record<string, unknown>) => StepPageSchema.parse(fields);

  it("says the reply showed, or what the page showed instead", () => {
    expect(shownWords(page({ shown: "Biscuit", check: { outcome: "passed" } }))).toEqual({
      held: true,
      text: "Shown on the page",
    });
    expect(
      shownWords(
        page({ shown: null, instead: "Couldn't load this reply", check: { outcome: "failed" } }),
      ),
    ).toEqual({ held: false, text: "Not shown — the page showed: “Couldn't load this reply”" });
    expect(shownWords(page({ shown: null, check: { outcome: "failed" } })).text).toBe(
      "Not shown on the page",
    );
    // Something else showed where the reply should have.
    expect(shownWords(page({ shown: "Hello again", check: { outcome: "failed" } })).text).toBe(
      "Not shown — the page showed: “Hello again”",
    );
  });

  it("shows the screenshot, enlarges it, and says code decided it on the page", () => {
    const steps = [
      PlanStepSchema.parse({
        kind: "say",
        message: "What's my dog called?",
        actual: { passed: true, text: "Sent" },
        page: {
          shown: null,
          instead: "Couldn't load this reply",
          shot: "turn-1.png",
          shot_url: "/v1/evals/runs/r1/cases/dog/tries/3/files/turn-1.png",
          check: { outcome: "failed", why: "never showed" },
        },
      }),
    ];
    render(<Steps steps={steps} names={{}} appName="relay-lite" result />);
    expect(
      screen.getByText("Not shown — the page showed: “Couldn't load this reply”"),
    ).toBeTruthy();
    expect(screen.getByText(/checked on the page/)).toBeTruthy();
    expect(screen.queryByText(/model/i)).toBeNull();
    const thumb = screen.getByAltText("The page after message 1") as HTMLImageElement;
    expect(thumb.getAttribute("src")).toBe("/v1/evals/runs/r1/cases/dog/tries/3/files/turn-1.png");
    fireEvent.click(
      screen.getByRole("button", { name: "Enlarge the screenshot of the page after message 1" }),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("After message 1, on the page")).toBeTruthy();
  });

  it("shows nothing of a page on a try through the API", () => {
    const steps = [PlanStepSchema.parse({ kind: "say", message: "hi", actual: { passed: true } })];
    render(<Steps steps={steps} names={{}} appName="app" result />);
    expect(screen.queryByTestId("page-shown")).toBeNull();
  });
});

describe("The page's console", () => {
  it("puts errors first, each in the order they came", () => {
    const page = TryPageSchema.parse({
      console: [
        { level: "warning", text: "slow", at: 0.2 },
        { level: "error", text: "TypeError: parts", at: 2.5 },
      ],
      failed_requests: [{ method: "GET", path: "/v1/me", status: 500, at: 0.1 }],
    });
    expect(pageLines(page).map((line) => [line.tone, line.text])).toEqual([
      ["error", "GET /v1/me: 500"],
      ["error", "TypeError: parts"],
      ["warning", "slow"],
    ]);
  });
});
