// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentMessageSchema, HealthSchema, MissingPieceSchema } from "../../lib/schema";
import { PageMapCard } from "../home/PageMapCard";
import { BrokenMapNote, MissingPieceNote } from "../shared/next-step";
import { WriterReply } from "../writer/WriterReply";
import { PageReach } from "./PageReach";
import { OpenEnvironment } from "./use-isolation";

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

it("a missing piece says what it is in a sentence, with the one action that fixes it", async () => {
  const said = "UI testing needs a browser, and there's none here: no Chromium and no Chrome.";
  const command = "uv run --project server playwright install chromium";
  serve({
    [`GET ${T}/page`]: reach({
      can_learn: false,
      why_not: `${said} Run: ${command}`,
      missing: { part: "browser", said, command },
    }),
  });
  render(<PageReach target="relay-lite" onAsk={vi.fn()} />);
  await screen.findByText(/Tests send each message through relay-lite's API/);
  fireEvent.click(screen.getByRole("button", { name: "Its page" }));
  expect(screen.getByText(said)).toBeTruthy();
  const field = screen.getByLabelText("The command that installs a browser") as HTMLInputElement;
  expect(field.value).toBe(command);
  expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
  cleanup();

  // No copy of the app: the one action is Set up testing, which opens the Test environment.
  const open = vi.fn();
  const copy =
    "relay-lite can't play through its page: that only happens on evalution's own copy of the app, and it has no proven isolation plan yet.";
  render(
    <OpenEnvironment.Provider value={open}>
      <MissingPieceNote piece={MissingPieceSchema.parse({ part: "copy", said: copy })} />
    </OpenEnvironment.Provider>,
  );
  expect(screen.getByText(copy)).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Set up testing" }));
  expect(open).toHaveBeenCalledTimes(1);
});

it("a try the page map ended shows the page then, what it lacked, and Re-learn the page", () => {
  const onRelearn = vi.fn();
  render(
    <BrokenMapNote
      target="relay-lite"
      missing={["button 'Send'"]}
      shot="/v1/files/turn-1.png"
      onRelearn={onRelearn}
    />,
  );
  expect(screen.getByText(/no longer matches relay-lite's page: it has no button 'Send'/));
  expect(screen.getByRole("img").getAttribute("src")).toBe("/v1/files/turn-1.png");
  fireEvent.click(screen.getByRole("button", { name: "Re-learn the page" }));
  expect(onRelearn.mock.calls[0]?.[0]).toContain("it has no button 'Send'");
});

it("Home says a broken page map, and what it lacks, with a way to learn it again", () => {
  const health = HealthSchema.parse({
    page: {
      status: "broken",
      stale_files: [],
      missing: ["button 'Send'"],
      shot: `${T}/page/shots/page-map-check.png`,
    },
  });
  const onAsk = vi.fn();
  render(<PageMapCard target="relay-lite" page={health.page} onAsk={onAsk} />);
  expect(
    screen.getByText(/no longer matches relay-lite's page: it has no button 'Send'/),
  ).toBeTruthy();
  expect(screen.getByRole("img").getAttribute("src")).toBe(`${T}/page/shots/page-map-check.png`);
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
