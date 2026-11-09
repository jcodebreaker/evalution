// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SkeletonEntrySchema } from "../../lib/schema";
import { AppSkeleton, useWords, windowWords } from "./AppSkeleton";

const T = "/v1/targets/relay-lite";
const NOW = Date.now() / 1000;

/** Answers `GET /v1/targets/relay-lite/skeleton` with `body` and `status`. */
function serve(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      expect(path.split("?")[0]).toBe(`${T}/skeleton`);
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const entry = (kind: string, name: string, path: string, line: number, extra = {}) => ({
  id: `${kind}:${path}:${line}:${name}`,
  kind,
  name,
  path,
  line,
  ...extra,
});

const FOUND = {
  fingerprint: "abc",
  entries: [
    entry("prompt", "SYSTEM_PROMPT", "app.py", 3, { text: "You greet people.", uses: 9 }),
    entry("tool", "greet_people", "app.py", 6, { args: ["name"], uses: 140 }),
    entry("tool", "search", "agents/worker.yaml", 4, {
      uses: 0,
      what: "setting",
      agents: 3,
      places: ["agents/planner.yaml:6", "agents/writer.yaml:7"],
    }),
    entry("tool", "fetch_page", "tools.py", 30, { uses: 0 }),
    entry("model", "messages.create", "app.py", 20, {
      model: "made-up-model",
      inside: "answer",
      uses: 1,
    }),
    entry("route", "POST /chat", "app.py", 11, { inside: "chat", uses: 0 }),
    entry("state", "MemoryStore", "lib/memory.py", 1, { what: "memory", uses: 0 }),
  ],
  unseen: [{ kind: "tool", name: "fetch_weather", calls: 2 }],
  window: { sessions: 50, asks: 120, since: NOW - 3 * 86_400, until: NOW },
};

it("lists the app's parts by kind, each with its file and line and its use in plain words", async () => {
  serve(FOUND);
  render(<AppSkeleton target="relay-lite" />);
  await screen.findByRole("region", { name: "Skeleton" });
  expect(screen.getByText("From the code, with use from 50 conversations this week")).toBeTruthy();
  const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
  expect(headings).toEqual([
    "Skeleton",
    "Prompts · 1",
    "Tools · 3",
    "Model calls · 1",
    "Routes · 1",
    "State · 1",
    "Seen in use, not found in the code",
  ]);
  const tools = within(screen.getByRole("list", { name: "Tools" })).getAllByRole("listitem");
  // The most used first, then what the most places name; a tool many agents list is one row.
  expect(tools.map((row) => row.textContent)).toEqual([
    "greet_people(name)app.py:6140 calls this week",
    "searchused by 3 agentsagents/worker.yaml:4 and 2 more placesnot used this week",
    "fetch_pagetools.py:30not used this week",
  ]);
  expect(within(tools[1] as HTMLElement).getByTitle(/agents\/planner.yaml:6/)).toBeTruthy();
  const models = screen.getByRole("list", { name: "Model calls" });
  expect(models.textContent).toBe(
    "made-up-modelmessages.create in answerapp.py:201 call this week",
  );
  // A route no trace names says nothing about use.
  expect(screen.getByRole("list", { name: "Routes" }).textContent).toBe(
    "POST /chatserved by chatapp.py:11",
  );
  const unseen = screen.getByRole("list", { name: "Seen in use, not found in the code" });
  expect(unseen.textContent).toBe("fetch_weathertool2 calls this week");
});

it("opens a prompt's text under it", async () => {
  serve(FOUND);
  render(<AppSkeleton target="relay-lite" />);
  const prompts = await screen.findByRole("list", { name: "Prompts" });
  const text = within(prompts).getByText("You greet people.");
  const details = text.closest("details");
  expect(details?.open).toBe(false);
  fireEvent.click(within(prompts).getByText("SYSTEM_PROMPT"));
  expect(details?.open).toBe(true);
  expect(within(prompts).getByText("sent 9 times this week")).toBeTruthy();
});

it("shows the skeleton with no traces, and a long group folded", async () => {
  const routes = Array.from({ length: 11 }, (_, n) =>
    entry("route", `GET /r${n}`, "app.py", n + 1),
  );
  serve({ entries: routes, unseen: [], window: null });
  render(<AppSkeleton target="relay-lite" />);
  const list = await screen.findByRole("list", { name: "Routes" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(8);
  expect(screen.getByText("From the code")).toBeTruthy();
  expect(list.textContent).not.toMatch(/request|call/);
  fireEvent.click(screen.getByRole("button", { name: "Show 3 more" }));
  expect(within(list).getAllByRole("listitem")).toHaveLength(11);
  expect(screen.queryByText("Seen in use, not found in the code")).toBeNull();
});

it("shows nothing for an app with no code folder", async () => {
  serve({ detail: "This app has no code folder, so there is no skeleton to show." }, 409);
  const { container } = render(<AppSkeleton target="relay-lite" />);
  await vi.waitFor(() => expect(container.querySelector(".animate-pulse")).toBeNull());
  expect(container.textContent).toBe("");
});

it("says use in plain words", () => {
  const tool = SkeletonEntrySchema.parse(entry("tool", "t", "a.py", 1, { uses: 1 }));
  const prompt = SkeletonEntrySchema.parse(entry("prompt", "p", "a.py", 1, { uses: 1 }));
  expect(useWords(tool, "today")).toBe("1 call today");
  expect(useWords(prompt, "today")).toBe("sent once today");
  expect(useWords({ ...tool, uses: null }, "today")).toBe("");
  const window = { sessions: 3, asks: 3, since: null, until: null };
  expect(windowWords(window)).toBe("in 3 conversations");
  expect(windowWords({ ...window, since: NOW - 20 * 86_400 }, NOW)).toBe("this month");
  expect(windowWords({ ...window, since: NOW - 40 * 86_400 }, NOW)).toBe("in the last 40 days");
});
