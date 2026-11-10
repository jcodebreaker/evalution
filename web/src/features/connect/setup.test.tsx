// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  DiscoveryViewSchema,
  IsolationViewSchema,
  type TargetDetail,
  UnderstandingViewSchema,
} from "../../lib/schema";
import { ConnectView } from "./ConnectView";
import { feedLines, stages } from "./SetupLive";

type Answer = { status?: number; body?: unknown };

function serve(routes: Record<string, Answer>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const { status = 200, body = {} } = routes[`${method} ${path.split("?")[0]}`] ?? {};
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const JOB = {
  id: "d1",
  folder: "/data/code/cartly",
  status: "working",
  steps: [
    { text: "Cloned acme/cartly#412 at a1b2c3d", state: "done" },
    { text: "Looking for where it takes messages", state: "doing" },
  ],
  questions: [],
  repository: { owner: "acme", repo: "cartly", pull: 412, ref: null, said: "acme/cartly#412" },
};

const MADE = { name: "cartly", adapter_label: "HTTP app" } as TargetDetail;

it("follows a pull request live: its repository and number as chips, the stages, the steps", async () => {
  serve({
    "POST /v1/targets/discover": { body: JOB },
    "GET /v1/targets/discover/d1": { body: JOB },
  });
  render(
    <ConnectView
      first
      onReady={async () => {}}
      onLearn={() => {}}
      onLearned={() => {}}
      onAsk={async () => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "owner/repo#412" }));
  fireEvent.submit(
    screen
      .getByLabelText("Folder, GitHub repository or address")
      .closest("form") as HTMLFormElement,
  );
  await screen.findByRole("heading", { name: "Setting up cartly" });
  expect(screen.getByText("acme/cartly")).toBeTruthy();
  expect(screen.getByText("#412")).toBeTruthy();
  const stagesList = screen.getByRole("list", { name: "Stages" });
  const states = within(stagesList)
    .getAllByRole("listitem")
    .map((item) => [item.getAttribute("aria-label"), item.dataset.state]);
  expect(states).toEqual([
    ["Code", "doing"],
    ["Talks", "waiting"],
    ["Test copy", "waiting"],
    ["Map", "waiting"],
  ]);
  const feed = screen.getByRole("list", { name: "What it's doing" });
  expect(within(feed).getByText("Cloned acme/cartly#412 at a1b2c3d")).toBeTruthy();
  expect(within(feed).getAllByRole("img", { name: "Working" })).toHaveLength(1);
  const first = screen.getByRole("button", { name: "Write the first test" }) as HTMLButtonElement;
  expect(first.disabled).toBe(true);
});

it("reads a discovery's repository, and an older server's without one", () => {
  expect(DiscoveryViewSchema.parse(JOB).repository?.pull).toBe(412);
  expect(DiscoveryViewSchema.parse({ ...JOB, repository: undefined }).repository).toBeNull();
});

it("says the copy's steps with their times, the keys it blanked, and each area as it lands", () => {
  const isolation = IsolationViewSchema.parse({
    target: "cartly",
    job: {
      state: "running",
      started: 1,
      steps: [
        { key: "find", label: "Find where it keeps things", state: "done", seconds: 3 },
        { key: "copy", label: "Copy cartly", state: "running", seconds: 9 },
        { key: "prove", label: "Check it resets cleanly", state: "waiting" },
      ],
    },
    outbound: [
      { name: "STRIPE_SECRET_KEY", kind: "payment", allowed: false },
      { name: "SENDGRID_API_KEY", kind: "mail", allowed: false },
      { name: "SLACK_WEBHOOK_URL", kind: "webhook", allowed: true },
    ],
  });
  const understanding = UnderstandingViewSchema.parse({
    card: { name: "cartly", numbers: {} },
    progress: {
      state: "learning",
      areas: [
        { id: "tools", title: "Tools", state: "done", files: 9 },
        { id: "agent", title: "Agent", state: "learning", files: 6 },
        {
          id: "memory",
          title: "Memory",
          state: "failed",
          reason: "the model's gateway timed out (504)",
        },
        { id: "web", title: "Web", state: "outside", files: 21 },
      ],
    },
  });
  const lines = feedLines({
    job: DiscoveryViewSchema.parse({ ...JOB, status: "ready" }),
    made: MADE,
    checked: true,
    isolation,
    understanding,
    writer: null,
    learned: false,
  });
  expect(lines.map((line) => [line.text, line.state, line.meta])).toEqual([
    ["Cloned acme/cartly#412 at a1b2c3d", "done", undefined],
    ["Looking for where it takes messages", "doing", undefined],
    ["Connected as HTTP app", "done", undefined],
    ["Sent a message, got a reply", "done", undefined],
    ["Find where it keeps things", "done", "3 s"],
    ["Copy cartly", "doing", "9 s"],
    ["Blanked 2 live keys in the copy", "blocked", "STRIPE_SECRET_KEY, SENDGRID_API_KEY"],
    ["Read Tools", "done", "9 files"],
    ["Reading Agent", "doing", undefined],
    ["Couldn't read Memory", "problem", "the model's gateway timed out (504)"],
  ]);
  const shown = stages({
    job: null,
    made: MADE,
    checked: true,
    isolation,
    understanding,
    modelChosen: true,
    learning: true,
    learned: false,
  });
  expect(shown.map((stage) => [stage.title, stage.state, stage.sub])).toEqual([
    ["Code", "done", "HTTP app"],
    ["Talks", "done", "replies"],
    ["Test copy", "doing", "1 of 3"],
    // The area outside the agent isn't counted.
    ["Map", "doing", "1 of 3"],
  ]);
});

it("waits for a model before it maps, and is done when the areas are learned", () => {
  const base = { job: null, made: MADE, checked: false, isolation: null, understanding: null };
  expect(stages({ ...base, modelChosen: false, learning: false, learned: false })[3]).toMatchObject(
    {
      state: "waiting",
      sub: "needs a model",
    },
  );
  expect(stages({ ...base, modelChosen: true, learning: false, learned: true })[3]).toMatchObject({
    state: "done",
  });
  expect(stages({ ...base, modelChosen: true, learning: false, learned: false })[2]).toMatchObject({
    title: "Test copy",
    sub: "needs you",
  });
});

it("a discovery that stopped says why and goes back to the field", async () => {
  const stopped = {
    ...JOB,
    status: "failed",
    steps: [{ text: "No such folder: ~/code/nope", state: "problem" }],
    repository: null,
  };
  serve({ "POST /v1/targets/discover": { body: stopped } });
  render(
    <ConnectView
      first={false}
      onReady={async () => {}}
      onLearn={() => {}}
      onLearned={() => {}}
      onAsk={async () => {}}
    />,
  );
  const field = screen.getByLabelText("Folder, GitHub repository or address");
  fireEvent.change(field, { target: { value: "~/code/nope" } });
  fireEvent.submit(field.closest("form") as HTMLFormElement);
  const needs = await screen.findByRole("generic", { name: "Needs you" });
  expect(within(needs).getByText("No such folder: ~/code/nope")).toBeTruthy();
  fireEvent.click(within(needs).getByRole("button", { name: "Try another" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Connect an app" })).toBeTruthy());
});
