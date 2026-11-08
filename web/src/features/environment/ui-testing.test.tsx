// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  CaseRowSchema,
  EstimateSchema,
  MissingPieceSchema,
  TestProofSchema,
} from "../../lib/schema";
import { RunConfirm } from "../runs/RunConfirm";
import { MissingPieceNote } from "../shared/next-step";
import { HowItPlays } from "../test/HowItPlays";
import { TestRow } from "../tests/TestRow";
import { PageReach } from "./PageReach";
import { UiOffer } from "./UiOffer";

type Call = { method: string; path: string; body: unknown };
let calls: Call[] = [];

/** Answers each `METHOD /path` from `routes`; anything else is an empty object. */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : null });
      const found = routes[`${method} ${path.split("?")[0]}`];
      const body = (typeof found === "function" ? found() : found) ?? {};
      return new Response(JSON.stringify(body), {
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const T = "/v1/targets/relay-lite";
const SCOPE = { target: "relay-lite", set: "main" };
const writes = () => calls.filter((c) => c.method !== "GET");

it("badges each test row with how it plays", () => {
  const row = (plays: string) =>
    CaseRowSchema.parse({ case: plays, name: `A ${plays} test`, verdict: null, plays });
  render(
    <>
      <TestRow row={row("page")} going={false} onOpen={() => null} onRun={() => null} />
      <TestRow row={row("api")} going={false} onOpen={() => null} onRun={() => null} />
    </>,
  );
  const chips = screen.getAllByTestId("plays-chip").map((chip) => chip.textContent);
  expect(chips).toEqual(["Page", "API"]);
  expect(screen.getByText("Page").getAttribute("title")).toMatch(/types each message/);
});

it("says in the Run dialog how each test plays, what it keeps and how long", async () => {
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 4,
    tries: 4,
    chats: 8,
    plays: [
      {
        how: "page",
        cases: ["a", "b", "c"],
        seconds: 40,
        kept: "screenshots of every message, video when a try fails",
        text: "3 tests through relay-lite's page, one at a time, about 40 s a try; screenshots of every message, video when a try fails.",
      },
      {
        how: "page",
        cases: ["d"],
        seconds: 40,
        kept: "no screenshots, video when a try fails",
        text: "1 test through relay-lite's page, one at a time, about 40 s a try; no screenshots, video when a try fails.",
      },
    ],
  });
  render(
    <RunConfirm target={{ scope: SCOPE, estimate }} onClose={() => null} onStarted={() => null} />,
  );
  await screen.findByText(/3 tests through relay-lite's page, one at a time/);
  expect(screen.getByText(/1 test through relay-lite's page.*no screenshots/)).toBeTruthy();
});

it("says an API run plays through the API, with nothing to capture", async () => {
  const estimate = EstimateSchema.parse({
    token: "t",
    cases: 2,
    tries: 2,
    chats: 2,
    plays: [
      {
        how: "api",
        cases: ["a", "b"],
        text: "2 tests through relay-lite's API, about 20 s a try.",
      },
    ],
  });
  render(
    <RunConfirm target={{ scope: SCOPE, estimate }} onClose={() => null} onStarted={() => null} />,
  );
  await screen.findByText("2 tests through relay-lite's API, about 20 s a try.");
  expect(screen.queryByText(/screenshots/)).toBeNull();
});

it("sets what each try keeps on the app's How tests reach card", async () => {
  const reach = (proof: Record<string, string>) => ({
    target: "relay-lite",
    plays: "page",
    managed: true,
    writable: true,
    can_learn: true,
    current: { status: "approved", map: [] },
    proof,
  });
  serve({
    [`GET ${T}/page`]: reach({ screenshots: "always", video: "failed" }),
    [`PUT ${T}/page/proof`]: reach({ screenshots: "off", video: "failed" }),
  });
  render(<PageReach target="relay-lite" />);
  await screen.findByText("What each try keeps");
  expect(
    screen.getByText(/Keeps a screenshot of every message and video when a try fails\./),
  ).toBeTruthy();
  const shots = screen.getByRole("group", { name: "Screenshots" });
  fireEvent.click(shots.querySelector("button:nth-child(3)") as HTMLElement);
  await screen.findByText(/Keeps no screenshots and video when a try fails\./);
  expect(writes()).toEqual([
    { method: "PUT", path: `${T}/page/proof`, body: { screenshots: "off" } },
  ]);
});

it("shows no proof setting for an app played through its API", async () => {
  serve({ [`GET ${T}/page`]: { target: "relay-lite", plays: "api", proof: null } });
  render(<PageReach target="relay-lite" />);
  await screen.findByRole("button", { name: "Its API", pressed: true });
  expect(screen.queryByTestId("page-proof")).toBeNull();
});

it("says under What it checks how a page test plays, and sets its own proof", async () => {
  const proof = (own: Record<string, string>, screenshots = "always") =>
    TestProofSchema.parse({
      screenshots,
      video: "failed",
      own,
      target: { screenshots: "always", video: "failed" },
    });
  const path = "/v1/evals/sets/main/cases/dog/proof";
  serve({ [`PUT ${path}`]: { plays: "page", proof: proof({ screenshots: "off" }, "off") } });
  render(<HowItPlays scope={SCOPE} id="dog" plays="page" proof={proof({})} />);
  expect(
    screen.getByText("Played through the page: types each message, checks it shows."),
  ).toBeTruthy();
  expect(screen.getAllByText("As the app keeps it")).toHaveLength(2);
  const shots = screen.getByRole("group", { name: "Screenshots" });
  fireEvent.click(shots.querySelector("button:nth-child(3)") as HTMLElement);
  await screen.findByText(/This test's own/);
  expect(writes()).toEqual([
    { method: "PUT", path: `${path}?target=relay-lite`, body: { screenshots: "off" } },
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Use the app's" }));
  await waitFor(() => expect(writes()).toHaveLength(2));
  expect(writes()[1]?.body).toEqual({ screenshots: null });
});

it("says an API test plays through the API, with no proof setting", () => {
  render(<HowItPlays scope={SCOPE} id="dog" plays="api" proof={null} />);
  expect(screen.getByText("Played through the API.")).toBeTruthy();
  expect(screen.queryByTestId("proof-control")).toBeNull();
});

it("offers UI testing once, and Not now turns it down on the server", async () => {
  const offer = (made: boolean) => ({
    target: "relay-lite",
    offer: made,
    found_in: "web/Chat.tsx",
    dismissed: !made,
    can_learn: true,
  });
  serve({
    [`GET ${T}/page/offer`]: offer(true),
    [`POST ${T}/page/offer/dismiss`]: offer(false),
  });
  render(<UiOffer target="relay-lite" />);
  await screen.findByText("relay-lite has a page. Test it the way people use it?");
  // The demo runs from the terminal: the card gives its command.
  const demo = screen.getByLabelText("The command that starts the demo") as HTMLInputElement;
  expect(demo.value).toBe("uv run --project server evalution demo ui");
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  await waitFor(() => expect(screen.queryByTestId("ui-offer")).toBeNull());
  expect(writes().map((c) => c.path)).toEqual([`${T}/page/offer/dismiss`]);
});

/** The page polls only while it's shown: jsdom says it's shown. */
function shown() {
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
}

const STEPS = (states: string[]) =>
  ["code", "look", "find", "propose", "rehearse"].map((key, index) => ({
    key,
    label: `The ${key} step`,
    state: states[index],
    seconds: states[index] === "done" ? 2 : null,
  }));

it("sets UI testing up on the card: the agent's steps, the rehearsal's video, one Approve", async () => {
  shown();
  const base = { target: "relay-lite", managed: true, writable: true, can_learn: true };
  const proposed = {
    status: "tried",
    map: [{ what: "Send", where: "button named “Send”" }],
    report: "Sent “Hello, this is a test.” through the page; the reply showed: “hi!”.",
    shots: [`${T}/page/shots/turn-1.png`],
    video: `${T}/page/shots/try.webm`,
  };
  const learn = (states: string[], state: string) => ({ state, started: 1, steps: STEPS(states) });
  serve({
    [`GET ${T}/page/offer`]: { target: "relay-lite", offer: true, can_learn: true },
    [`POST ${T}/page/learn`]: {
      ...base,
      plays: "api",
      learn: learn(["done", "running", "waiting", "waiting", "waiting"], "running"),
    },
    [`GET ${T}/page`]: {
      ...base,
      plays: "api",
      proposed,
      learn: learn(Array(5).fill("done"), "done"),
    },
    [`POST ${T}/page/approve`]: {
      ...base,
      plays: "page",
      current: { status: "approved", map: proposed.map },
      learn: learn(Array(5).fill("done"), "done"),
    },
  });
  render(<UiOffer target="relay-lite" />);
  // The person's yes to one message is said before anything starts.
  await screen.findByText(/then tries its map with one message there/);
  fireEvent.click(screen.getByRole("button", { name: "Set up UI testing" }));

  await screen.findByText("The agent is learning relay-lite's page");
  const states = screen.getAllByRole("listitem").map((item) => item.dataset.state);
  expect(states).toEqual(["done", "running", "waiting", "waiting", "waiting"]);
  expect(writes()).toEqual([{ method: "POST", path: `${T}/page/learn`, body: { confirm: true } }]);

  // The card follows it to the map that showed its reply, without the chat.
  await screen.findByText("The agent's map, waiting for your approval", undefined, {
    timeout: 3000,
  });
  const video = screen.getByLabelText("A video of the rehearsal on the page");
  expect(video.getAttribute("src")).toBe(`${T}/page/shots/try.webm`);
  expect((screen.getByRole("img") as HTMLImageElement).src).toContain("turn-1.png");
  expect(screen.getByText(/the reply showed: “hi!”/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await screen.findByTestId("ui-approved");
  expect(writes().map((c) => c.path)).toEqual([`${T}/page/learn`, `${T}/page/approve`]);
});

it("says what the agent said when it found no map, and offers to try again", async () => {
  const failed = {
    state: "failed",
    started: 1,
    steps: STEPS(["failed", "waiting", "waiting", "waiting", "waiting"]),
    error: "The page has no message box I can find.",
  };
  serve({
    [`GET ${T}/page/offer`]: { target: "relay-lite", offer: true, can_learn: true },
    [`POST ${T}/page/learn`]: { target: "relay-lite", can_learn: true, learn: failed },
  });
  render(<UiOffer target="relay-lite" />);
  fireEvent.click(await screen.findByRole("button", { name: "Set up UI testing" }));
  await screen.findByText(/The page has no message box I can find\./);
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
});

it("installs a missing browser from the offer, with its progress, instead of a command", async () => {
  shown();
  const said = "UI testing needs a browser, and there's none here: no Chromium and no Chrome.";
  const command = "uv run --project server playwright install chromium";
  const piece = { part: "browser", said, command };
  const job = (state: string, step: string) => ({
    state,
    started: 1,
    steps: [{ key: "browser", label: "Download Chromium", state: step }],
    lines: ["Downloading Chromium 130 MiB", "50%"],
  });
  let installed = false;
  serve({
    [`GET ${T}/page/offer`]: () => ({
      target: "relay-lite",
      offer: true,
      can_learn: installed,
      why_not: installed ? null : `${said} Run: ${command}`,
      missing: installed ? null : piece,
    }),
    "GET /v1/settings/ui-testing": () =>
      installed
        ? { ready: true, missing: [], can_install: false, job: job("done", "done") }
        : { ready: false, missing: [piece], can_install: true, job: null },
    "POST /v1/settings/ui-testing/install": () => {
      installed = true;
      return { ready: false, missing: [piece], can_install: true, job: job("running", "running") };
    },
  });
  render(<UiOffer target="relay-lite" />);
  await screen.findByText(said);
  // The command stays, as the way by hand, behind the button.
  expect(screen.getByText("Or run it yourself")).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", { name: "Install" }));
  await screen.findByText("Download Chromium");
  expect(screen.getByLabelText("What the install says").textContent).toContain("50%");
  expect(writes().map((c) => c.path)).toEqual(["/v1/settings/ui-testing/install"]);
  // Once it's in, the offer is read again: the agent can learn the page now.
  await screen.findByText(/then tries its map with one message there/, undefined, {
    timeout: 3000,
  });
  expect(screen.getByRole("button", { name: "Set up UI testing" })).toBeTruthy();
});

it("says how instead where the server can't install it", async () => {
  const said = "UI testing needs Playwright, which this install of evalution doesn't have.";
  const command = "uv sync --project server --extra page";
  const why = "evalution isn't running from its own checkout under uv.";
  serve({
    "GET /v1/settings/ui-testing": {
      ready: false,
      missing: [{ part: "playwright", said, command }],
      can_install: false,
      why_not_install: why,
    },
  });
  render(
    <MissingPieceNote piece={MissingPieceSchema.parse({ part: "playwright", said, command })} />,
  );
  await screen.findByText(why);
  expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  const field = screen.getByLabelText("The command that installs Playwright") as HTMLInputElement;
  expect(field.value).toBe(command);
});

it("makes no offer where the server makes none", async () => {
  serve({ [`GET ${T}/page/offer`]: { target: "relay-lite", offer: false } });
  render(<UiOffer target="relay-lite" />);
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(screen.queryByTestId("ui-offer")).toBeNull();
});
