// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentMessageSchema, HealthSchema } from "../../lib/schema";
import { PageMapCard } from "../home/PageMapCard";
import { WriterReply } from "../writer/WriterReply";
import { PageReach } from "./PageReach";

type Call = { method: string; path: string };
let calls: Call[] = [];

/** Answers each `METHOD /path` from `routes`; anything else is an empty object. */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, path });
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
const MAP = [
  { what: "Opens", where: "/chat" },
  { what: "Message box", where: "textbox named “Message”" },
  { what: "Send", where: "button named “Send”" },
];
const kept = (extra: Record<string, unknown>) => ({ status: "approved", map: MAP, ...extra });
const reach = (extra: Record<string, unknown>) => ({
  target: "relay-lite",
  plays: "api",
  managed: true,
  can_learn: true,
  ...extra,
});

it("shows the agent's tried map in plain words with its screenshot, and approves it once", async () => {
  const proposed = kept({
    status: "tried",
    report: "Sent “Hello, this is a test.” through the page; the reply showed: “hi!”.",
    shots: [`${T}/page/shots/turn-1.png`],
  });
  serve({
    [`GET ${T}/page`]: reach({ proposed }),
    [`POST ${T}/page/approve`]: reach({ plays: "page", current: kept({}), can_undo: true }),
  });
  render(<PageReach target="relay-lite" />);
  await screen.findByText("The agent's map, waiting for your approval");
  expect(screen.getByRole("button", { name: "Its page", pressed: true })).toBeTruthy();
  expect(screen.getByText("button named “Send”")).toBeTruthy();
  expect(screen.getByText(/the reply showed: “hi!”/)).toBeTruthy();
  const shot = screen.getByRole("img") as HTMLImageElement;
  expect(shot.src).toContain(`${T}/page/shots/turn-1.png`);
  expect(screen.getAllByRole("button", { name: /Approve/ })).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await screen.findByText(/Tests type and click on the page as a person would/);
  expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
    `${T}/page/approve`,
  ]);
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getByRole("button", { name: "Undo the last map" })).toBeTruthy();
});

it("says a map a commit left out of date, and asks the agent to learn the page again", async () => {
  serve({
    [`GET ${T}/page`]: reach({
      plays: "page",
      current: kept({ status: "stale", stale_files: ["web/Chat.tsx"] }),
    }),
  });
  const onAsk = vi.fn();
  render(<PageReach target="relay-lite" onAsk={onAsk} />);
  await screen.findByText(/Out of date: a commit changed web\/Chat.tsx/);
  fireEvent.click(screen.getByRole("button", { name: "Re-learn the page" }));
  await waitFor(() => expect(onAsk).toHaveBeenCalledTimes(1));
  expect(onAsk.mock.calls[0]?.[0]).toContain("a commit changed web/Chat.tsx");
});

it("says why the page can't be learned without the copy, and offers nothing to start", async () => {
  const why =
    "relay-lite's page is only ever opened on evalution's own copy of the app, and it has no isolation plan yet.";
  serve({ [`GET ${T}/page`]: reach({ can_learn: false, why_not: why }) });
  render(<PageReach target="relay-lite" onAsk={vi.fn()} />);
  await screen.findByText(/Tests send each message through relay-lite's API/);
  fireEvent.click(screen.getByRole("button", { name: "Its page" }));
  expect(screen.getByText(why)).toBeTruthy();
  const learn = screen.getByRole("button", { name: "Learn the page" }) as HTMLButtonElement;
  expect(learn.disabled).toBe(true);
});

it("Home says a broken page map, and what it lacks, with a way to learn it again", () => {
  const health = HealthSchema.parse({
    page: { status: "broken", stale_files: [], missing: ["button 'Send'"] },
  });
  const onAsk = vi.fn();
  render(<PageMapCard target="relay-lite" page={health.page} onAsk={onAsk} />);
  expect(
    screen.getByText(/no longer matches relay-lite's page: it has no button 'Send'/),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Re-learn the page" }));
  expect(onAsk.mock.calls[0]?.[0]).toContain("it has no button 'Send'");
  render(<PageMapCard target="relay-lite" page={HealthSchema.parse({}).page} onAsk={onAsk} />);
  expect(screen.getAllByTestId("page-map-card")).toHaveLength(1);
});

it("the writer's proposed map shows where each entry comes from, and trying it is approved", () => {
  const message = AgentMessageSchema.parse({
    id: "m1",
    role: "agent",
    at: 0,
    text: "Here is how tests would reach the page.",
    page_map: {
      id: "g1",
      version: 1,
      status: "draft",
      target: "relay-lite",
      described: MAP,
      evidence: [{ entry: "actions.send", file: "web/Chat.tsx", line: 7, tried: true }],
    },
  });
  const onApprove = vi.fn();
  render(
    <WriterReply
      message={message}
      latest
      runs={[]}
      runLabel={(run) => run}
      testName={(id) => id}
      going={false}
      onReply={vi.fn()}
      onApprove={onApprove}
      onSwapLine={vi.fn()}
      onRun={vi.fn()}
      onRunTest={vi.fn()}
      onTest={vi.fn()}
      onLearned={vi.fn()}
    />,
  );
  expect(screen.getByText("web/Chat.tsx:7, found on the page")).toBeTruthy();
  expect(screen.getByText("Trying it sends one message to evalution's copy")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Approve and try it" }));
  expect(onApprove).toHaveBeenCalledWith("g1");
});
