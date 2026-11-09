// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  CaseRowSchema,
  EstimateSchema,
  MissingPieceSchema,
  TestProofSchema,
} from "../../lib/schema";
import { RunConfirm } from "../runs/RunConfirm";
import { MissingPieceNote } from "../shared/next-step";
import { UiTestingStatus } from "../shared/ui-install";
import { HowItPlays } from "../test/HowItPlays";
import { TestRow } from "../tests/TestRow";
import { playsLine } from "../tests/TestsView";
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

it("says over the list how the tests play", () => {
  const rows = (plays: string[]) =>
    plays.map((way, i) =>
      CaseRowSchema.parse({ case: `t${i}`, name: "t", verdict: null, plays: way }),
    );
  expect(playsLine("relay-lite", rows(["page", "page"]))).toBe(
    "Played through relay-lite's page: each test types each message there, and checks it shows.",
  );
  expect(playsLine("relay-lite", rows(["api"]))).toBe(
    "Played through relay-lite's API: each test sends its messages straight to it.",
  );
});

it("lists in Settings each piece UI testing needs, and installs what's missing with progress", async () => {
  shown();
  const browser = {
    part: "browser",
    said: "UI testing needs a browser.",
    command: "uv run playwright install chromium",
  };
  const job = {
    state: "running",
    started: 1,
    steps: [{ key: "browser", label: "Download Chromium", state: "running" }],
    lines: ["Downloading Chromium 141.0", "|■■■■■■■■■■   |  62% of 124.3 MiB"],
  };
  let started = false;
  serve({
    "GET /v1/settings/ui-testing": () => ({
      ready: false,
      missing: [browser],
      parts: { playwright: true, browser: false, ffmpeg: true },
      can_install: true,
      job: started ? job : null,
    }),
    "POST /v1/settings/ui-testing/install": () => {
      started = true;
      return {
        ready: false,
        missing: [browser],
        parts: { playwright: true, browser: false, ffmpeg: true },
        can_install: true,
        job,
      };
    },
  });
  render(<UiTestingStatus />);
  const parts = await screen.findByRole("list", { name: "What UI testing needs" });
  const state = () =>
    [...parts.querySelectorAll("li")].map((li) => [li.dataset.part, li.dataset.state]);
  expect(state()).toEqual([
    ["playwright", "done"],
    ["browser", "missing"],
    ["ffmpeg", "done"],
  ]);
  expect(screen.getByText(/Without it, tests still play, with screenshots only/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  await screen.findByRole("progressbar", { name: "Installing A browser" });
  expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("62");
  expect(state()[1]).toEqual(["browser", "running"]);
  expect(screen.getByLabelText("What the install says").textContent).toContain("62%");
  expect(screen.getByText("Or run it yourself")).toBeTruthy();
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
  // A group for each way they play, badged, with how they play, how long and what's kept.
  await screen.findByText("3 tests through relay-lite's page");
  const groups = screen.getAllByRole("listitem");
  expect(groups.map((group) => group.textContent)).toEqual([
    "Page3 tests through relay-lite's pageOne at a time, about 40 s a try. Screenshots of every message, video when a try fails.",
    "Page1 test through relay-lite's pageOne at a time, about 40 s a try. No screenshots, video when a try fails.",
  ]);
  // Played one at a time: the button says the rough total (4 tries at about 40 s).
  expect(screen.getByRole("button", { name: "Run 4 tests · about 3 min" })).toBeTruthy();
});

it("sets what a page try keeps right in the Run dialog, and says the run again", async () => {
  const line = (kept: string) => ({
    how: "page",
    cases: ["a"],
    seconds: 30,
    kept,
    text: `1 test through relay-lite's page, one at a time, about 30 s a try; ${kept}.`,
  });
  let proof = { screenshots: "always", video: "failed" };
  serve({
    "POST /v1/evals/runs/estimate": () => ({
      token: "t",
      cases: 1,
      tries: 1,
      chats: 1,
      plays: [
        line(
          proof.video === "off"
            ? "screenshots of every message, no video"
            : "screenshots of every message, video when a try fails",
        ),
      ],
    }),
    [`GET ${T}/page`]: () => ({ target: "relay-lite", plays: "page", writable: true, proof }),
    [`PUT ${T}/page/proof`]: () => {
      proof = { ...proof, video: "off" };
      return { target: "relay-lite", plays: "page", writable: true, proof };
    },
  });
  render(<RunConfirm target={{ scope: SCOPE }} onClose={() => null} onStarted={() => null} />);
  await screen.findByText(/, video when a try fails\./);
  const video = await screen.findByRole("group", { name: "Video" });
  expect(screen.getByText(/A test can set its own under "What it checks"/)).toBeTruthy();
  fireEvent.click(within(video).getByRole("button", { name: "Off" }));
  // The same setting the Test environment card writes, and the dialog says what's kept now.
  await screen.findByText(/, no video\./);
  expect(calls.filter((c) => c.method === "PUT")).toEqual([
    { method: "PUT", path: `${T}/page/proof`, body: { video: "off" } },
  ]);
  expect(calls.filter((c) => c.path === "/v1/evals/runs/estimate")).toHaveLength(2);
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
  expect(screen.getByText("API")).toBeTruthy();
  // Through the API tests may play side by side: no total is guessed.
  expect(screen.getByRole("button", { name: "Run 2 tests" })).toBeTruthy();
  expect(screen.queryByTestId("run-proof")).toBeNull();
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
  expect(screen.getByText("New for relay-lite")).toBeTruthy();
  // Set up, Not now and Try the demo first, side by side.
  const acts = screen.getByRole("button", { name: "Set up UI testing" }).parentElement;
  expect([...(acts?.querySelectorAll("button") ?? [])].map((b) => b.textContent)).toEqual([
    "Set up UI testing",
    "Not now",
    "Try the demo first",
  ]);
  // The demo runs from the terminal: the card gives its command.
  expect(screen.getByTestId("ui-demo").hidden).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Try the demo first" }));
  expect(screen.getByTestId("ui-demo").hidden).toBe(false);
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
      can_undo: true,
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
  await screen.findByText("Approve how tests use the page", undefined, {
    timeout: 3000,
  });
  expect(screen.getByRole("button", { name: "Re-learn the page" })).toBeTruthy();
  const video = screen.getByLabelText("A video of the rehearsal on the page");
  expect(video.getAttribute("src")).toBe(`${T}/page/shots/try.webm`);
  expect((screen.getByRole("img") as HTMLImageElement).src).toContain("turn-1.png");
  expect(screen.getByText(/the reply showed: “hi!”/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await screen.findByTestId("ui-approved");
  expect(writes().map((c) => c.path)).toEqual([`${T}/page/learn`, `${T}/page/approve`]);
  // Undo is on the same card.
  expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
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
