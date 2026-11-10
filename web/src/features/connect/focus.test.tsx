// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DiscoverySchema } from "../../lib/schema";
import { FocusChip } from "../learned/FocusChip";
import { ALL, FocusPick } from "./FocusPick";

type Call = { method: string; path: string; body: unknown };
let calls: Call[] = [];

function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : undefined });
      const body = routes[`${method} ${path.split("?")[0]}`];
      return new Response(JSON.stringify(body ?? {}), {
        status: body === undefined ? 404 : 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const CHOICES = [
  {
    value: "planner",
    label: "planner",
    hint: "",
    where: "agents/planner.yaml:1",
    uses: 12,
    tools: 4,
  },
  { value: "writer", label: "writer", hint: "", where: "agents/writer.yaml:1", uses: 0, tools: 2 },
  { value: ALL, label: "All of them", hint: "6 agents", where: "", uses: null, tools: 9 },
];

it("reads a question with choices, and one without", () => {
  const found = DiscoverySchema.parse({
    id: "a",
    folder: "/x",
    status: "ready",
    steps: [],
    questions: [
      { key: "chat", text: "Which address takes chat messages?", hint: "/chat" },
      {
        key: "focus",
        text: "Which agent do you want to test?",
        hint: "All of them",
        choices: CHOICES,
      },
    ],
  });
  const [chat, agent] = found.questions;
  expect(chat?.choices).toBeUndefined();
  expect(agent?.choices?.map((c) => c.value)).toEqual(["planner", "writer", ALL]);
});

it("answers with one click, showing where each agent is and how often it was used", () => {
  const picked = vi.fn();
  render(<FocusPick label="Which agent do you want to test?" choices={CHOICES} onPick={picked} />);
  expect(screen.getByText("agents/planner.yaml:1")).toBeTruthy();
  expect(screen.getByText("12 uses")).toBeTruthy();
  // None picked yet: all of them hold.
  expect(screen.getByRole("button", { name: /All of them/ }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  fireEvent.click(screen.getByRole("button", { name: /writer/ }));
  expect(picked).toHaveBeenCalledWith("writer");
});

it("shows the agent tested and saves another", async () => {
  const T = "/v1/targets/platform";
  const target = {
    name: "platform",
    adapter: "http",
    options: {},
    phoenix: null,
    code: { path: "/x", ignore: [], include: [], focus: "planner" },
  };
  serve({
    [`GET ${T}/agents`]: { focus: "planner", asks: true, choices: CHOICES },
    [`GET ${T}`]: target,
    [`PUT ${T}`]: target,
  });
  render(<FocusChip target="platform" />);
  await screen.findByText("Testing: planner");
  fireEvent.click(screen.getByRole("button", { name: "Change" }));
  fireEvent.click(screen.getByRole("button", { name: /writer/ }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT")?.body as { code?: { focus?: string } };
  expect(put.code?.focus).toBe("writer");
});

it("says nothing on an app with few agents and no focus", async () => {
  serve({ "GET /v1/targets/small/agents": { focus: null, asks: false, choices: [] } });
  const { container } = render(<FocusChip target="small" />);
  await waitFor(() => expect(calls.length).toBe(1));
  expect(container.textContent).toBe("");
});
