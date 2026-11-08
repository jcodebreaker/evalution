// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CaseRowSchema, EstimateSchema, TestProofSchema } from "../../lib/schema";
import { RunConfirm } from "../runs/RunConfirm";
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
      const body = routes[`${method} ${path.split("?")[0]}`] ?? {};
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
  const onAsk = vi.fn();
  render(<UiOffer target="relay-lite" onAsk={onAsk} />);
  await screen.findByText("relay-lite has a page. Test it the way people use it?");
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  await waitFor(() => expect(screen.queryByTestId("ui-offer")).toBeNull());
  expect(writes().map((c) => c.path)).toEqual([`${T}/page/offer/dismiss`]);
  expect(onAsk).not.toHaveBeenCalled();
});

it("hands the agent the page to learn from the offer", async () => {
  serve({
    [`GET ${T}/page/offer`]: { target: "relay-lite", offer: true, can_learn: true },
  });
  const onAsk = vi.fn();
  render(<UiOffer target="relay-lite" onAsk={onAsk} />);
  fireEvent.click(await screen.findByRole("button", { name: "Set up UI testing" }));
  await screen.findByText(/The agent is learning the page/);
  expect(onAsk).toHaveBeenCalledWith(
    expect.stringMatching(/^Learn how tests reach relay-lite's page/),
  );
});

it("makes no offer where the server makes none", async () => {
  serve({ [`GET ${T}/page/offer`]: { target: "relay-lite", offer: false } });
  render(<UiOffer target="relay-lite" />);
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(screen.queryByTestId("ui-offer")).toBeNull();
});
