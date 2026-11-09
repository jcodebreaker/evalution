// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CompareSchema } from "../../lib/schema";
import { notTakenOf, pageNextStep } from "../../lib/verdict";
import { PageBeside, PageNextStep } from "./PageBeside";
import { VerdictBanner } from "./VerdictBanner";

afterEach(cleanup);

const APP = "relay-lite";
const TABLE =
  "Here's a comparison:\n\n| Trait | Cats | Dogs |\n|---|---|---|\n| Walks | No | Yes |";

/** A try of "A Markdown table shows", played through the page: the mockup's screen 8. */
const tableTry = (fields: Record<string, unknown> = {}) =>
  CompareSchema.parse({
    name: "A Markdown table shows",
    run: "20261108-181204-a3c1",
    try: 1,
    tries: [{ n: 1, outcome: "failed", reason: "the page showed …, not turn 1's reply" }],
    outcome: "failed",
    called: true,
    layers: { stored: "pass", answered: "pass", shown: "fail" },
    steps: [
      {
        kind: "say",
        message: "Compare cats and dogs in a table",
        actual: { passed: true, text: "Sent", reply: TABLE },
        page: {
          shown: "Here's a comparison:",
          shot: "turn-1.png",
          shot_url: "/files/turn-1.png",
          check: { outcome: "failed", why: "the page showed …" },
          part: { shown: "Here's a comparison", missing: "Trait Cats Dogs Walks No Yes" },
        },
      },
    ],
    page: {
      without_page: "passed",
      health: { outcome: "passed", why: "no console errors" },
      shown_alone: "fail",
      video: { name: "try.webm", url: "/files/try.webm" },
    },
    ...fields,
  });

/** Run 20261108-162620-839b's try, as stored: no reply, the 503 counted against the page. */
const refusedTry = (page: Record<string, unknown> = {}) =>
  CompareSchema.parse({
    name: "A code block shows",
    run: "20261108-162620-839b",
    try: 1,
    tries: [{ n: 1, outcome: "failed", reason: null }],
    outcome: "failed",
    label: "Failed check",
    called: false,
    reason: "the app answered a request from the page with an error, 503",
    layers: { stored: "absent", answered: "unjudged", shown: "fail" },
    steps: [
      {
        kind: "say",
        message: "Show me a code block",
        actual: { passed: false, text: "" },
        page: { shown: null, shot_url: "/files/turn-1.png", check: { outcome: "unjudged" } },
      },
    ],
    page: {
      without_page: "unjudged",
      health: { outcome: "failed", why: "1 console error(s), …" },
      shown_alone: "unjudged",
      ...page,
    },
  });

const pills = () =>
  within(screen.getByRole("list", { name: "Which layer held" }))
    .getAllByRole("listitem")
    .map((item) => item.getAttribute("title"));

describe("The banner of a failed page try", () => {
  it("says the page's health apart from what showed", () => {
    render(<VerdictBanner shown={tableTry()} runLabel={() => "Run"} onTry={() => null} />);
    expect(pills()).toEqual([
      "Stored: held",
      "Answered: held",
      "Shown: failed",
      "Page healthy: held",
    ]);
  });

  it("blames the app when it never answered, on a try stored before the server said which", () => {
    const shown = refusedTry();
    expect(notTakenOf(shown)).toEqual({ status: null, silent: true });
    render(<VerdictBanner shown={shown} runLabel={() => "Run"} onTry={() => null} />);
    expect(screen.getByText("The app never answered the message")).toBeTruthy();
    expect(screen.queryByText(/Failed check/)).toBeNull();
    expect(pills()).toEqual([
      "Answered: couldn't judge",
      "Shown: couldn't judge",
      "Page healthy: failed",
    ]);
  });

  it("doesn't blame the app for a try that answered, or that the page map ended", () => {
    expect(notTakenOf(tableTry())).toBeNull();
    expect(notTakenOf(refusedTry({ broken_map: { missing: "button named Send" } }))).toBeNull();
    expect(
      notTakenOf({
        ...refusedTry(),
        called: true,
        layers: { stored: "absent", answered: "pass", shown: "fail" },
      }),
    ).toBeNull();
  });
});

describe("What the page showed beside what the app answered", () => {
  it("lays the screenshot and video beside the reply, with how far the page showed it", () => {
    render(<PageBeside shown={tableTry()} />);
    const beside = screen.getByTestId("page-beside");
    expect(within(beside).getByText("What the page showed")).toBeTruthy();
    expect(within(beside).getByText("What the app answered")).toBeTruthy();
    expect(within(beside).getByText(/\| Trait \| Cats \| Dogs \|/)).toBeTruthy();
    expect(
      (within(beside).getByAltText("The page after message 1") as HTMLImageElement).src,
    ).toContain("/files/turn-1.png");
    expect(within(beside).getByLabelText("Recording of the try on the page")).toBeTruthy();
    expect(screen.getByTestId("page-part").textContent).toBe(
      "Shown on the page: Here's a comparison · Missing: Trait Cats Dogs Walks No Yes",
    );
    fireEvent.click(screen.getByRole("button", { name: /Enlarge the screenshot/ }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("says there was no reply, and whose, when the app never answered", () => {
    render(<PageBeside shown={refusedTry()} />);
    expect(screen.getByText("No reply: the app never answered the message.")).toBeTruthy();
    expect(screen.queryByTestId("page-part")).toBeNull();
  });

  it("shows nothing for a passed try, or one the page map ended", () => {
    render(<PageBeside shown={tableTry({ outcome: "passed", passed: true })} />);
    render(<PageBeside shown={refusedTry({ broken_map: { missing: "button named Send" } })} />);
    expect(screen.queryByTestId("page-beside")).toBeNull();
  });
});

describe("The one next step", () => {
  it("the page dropped the table: the page's to fix, then run this test again", () => {
    const onRun = vi.fn();
    render(<PageNextStep shown={tableTry()} appName={APP} going={false} onRun={onRun} />);
    expect(screen.getByTestId("next-step").textContent).toBe(
      "Next step: The reply is right, so this is the page's to fix: it didn't show “Trait Cats Dogs Walks No Yes”. Change relay-lite's page, then run this test again.Run this test again",
    );
    fireEvent.click(screen.getByRole("button", { name: "Run this test again" }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("the app refused the message: the app's, run again", () => {
    const shown = refusedTry({ not_taken: { status: 503 }, health: null });
    const next = pageNextStep({ ...shown, retried: [{ what: "app", why: "busy" }] }, APP);
    expect(next).toEqual({
      text: "relay-lite refused the page's message (503), so this is relay-lite's, not the page's. It was already tried a second time. Run it again once relay-lite answers.",
      button: "Run again",
    });
    expect(pageNextStep(refusedTry(), APP)?.text).toMatch(
      /^relay-lite never answered the page's message, so this is relay-lite's/,
    );
  });

  it("only the page's health failed: says what, in the reason's plain words", () => {
    const shown = tableTry({
      reason: "the app answered a request from the page with an error, 503",
      steps: [{ kind: "say", page: { shown: "x", check: { outcome: "passed" } } }],
      page: {
        without_page: "passed",
        shown_alone: "pass",
        health: { outcome: "failed", why: "1 failed request(s), first GET /v1/me: 503" },
      },
    });
    expect(pageNextStep(shown, APP)).toEqual({
      text: "Every reply showed, but the app answered a request from the page with an error, 503. Fix that, then run this test again.",
      button: "Run this test again",
    });
  });

  it("says none for a pass, a broken page map, a try through the API, or a wrong answer", () => {
    expect(pageNextStep(tableTry({ outcome: "passed" }), APP)).toBeNull();
    expect(
      pageNextStep(refusedTry({ broken_map: { missing: "button named Send" } }), APP),
    ).toBeNull();
    expect(pageNextStep(tableTry({ page: null }), APP)).toBeNull();
    const wrong = tableTry({ layers: { stored: "absent", answered: "fail", shown: "fail" } });
    expect(
      pageNextStep(
        { ...wrong, page: wrong.page && { ...wrong.page, without_page: "failed" } },
        APP,
      ),
    ).toBeNull();
  });
});
