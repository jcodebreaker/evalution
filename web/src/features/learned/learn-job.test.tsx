// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LearnJobSchema, UnderstandingViewSchema } from "../../lib/schema";
import { AppView } from "./AppView";
import { Learning } from "./Learning";

const T = "/v1/targets/bot";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const DIAGRAM = {
  nodes: [
    {
      id: "person",
      kind: "person",
      title: "A person",
      evidence: [{ kind: "code", ref: "app.py" }],
    },
    { id: "agent", kind: "agent", title: "The agent", evidence: [{ kind: "code", ref: "app.py" }] },
  ],
  edges: [{ from: "person", to: "agent", asks: 3 }],
};

const area = (id: string, state: string) => ({
  id,
  title: `The ${id} part`,
  state,
  weight: 3,
  files: 4,
  learned_at: null,
});

/** The facts level: card and diagram from the code, no area learned. */
const FACTS = {
  version: 1,
  card: { name: "bot", one_line: "A bot that greets.", numbers: { tools: 2 } },
  diagram: DIAGRAM,
  sections: [],
  overview: { state: "facts", basis: "the code and 12 conversations" },
  progress: {
    state: "stopped",
    overview: "facts",
    areas: [area("root", "done"), area("lib", "due"), area("web", "listed")],
  },
};

const JOB = {
  id: "l1",
  target: "bot",
  state: "stopped",
  interrupted: true,
  areas_asked: [],
  everything: true,
  started: 1,
  progress: null,
};

/** Answers the page's reads and records what it starts. */
function serve(job: unknown) {
  const posted: { path: string; body: unknown }[] = [];
  const answer = (data: unknown, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  const fetch = vi.fn(async (path: string, init: RequestInit = {}) => {
    const at = path.split("?")[0];
    if (init.method === "POST") {
      posted.push({ path: at ?? "", body: JSON.parse(String(init.body ?? "{}")) });
      return answer({ ...JOB, state: "running", interrupted: false });
    }
    if (at === `${T}/understanding`) return answer(UnderstandingViewSchema.parse(FACTS));
    if (at === `${T}/learn`) return answer(job);
    return answer({}, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return posted;
}

const page = () => (
  <AppView
    target="bot"
    onAdd={async () => undefined}
    onSaved={async () => undefined}
    onRemoved={() => undefined}
  />
);

it("says the facts level honestly, in a pill, and offers Learn this area on an area not learned", async () => {
  const posted = serve(null);
  render(page());
  expect(
    await screen.findByText(
      "From the code and 12 conversations. Areas are read when a test needs them.",
    ),
  ).toBeTruthy();
  expect(screen.queryByText("First pass")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: /^Areas/ }));
  const lib = screen.getByRole("listitem", { name: "The lib part" });
  expect(within(lib).getAllByText("Not learned yet").length).toBeGreaterThan(0);
  fireEvent.click(within(lib).getByRole("button", { name: "Learn this area" }));
  await waitFor(() => expect(posted.map((p) => p.path)).toContain(`${T}/areas/lib/learn`));
});

it("starts the learn job from Learn again, not the chat", async () => {
  const posted = serve(null);
  render(page());
  fireEvent.click(await screen.findByRole("button", { name: "Learn again" }));
  await waitFor(() => expect(posted).toEqual([{ path: `${T}/learn`, body: {} }]));
});

it("offers Go on after a server stopped under a learn, asking what it asked", async () => {
  const posted = serve(JOB);
  render(page());
  fireEvent.click(await screen.findByRole("tab", { name: /^Areas/ }));
  expect(await screen.findByText(/The server stopped while it learned/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Go on" }));
  await waitFor(() =>
    expect(posted).toEqual([{ path: `${T}/learn`, body: { areas: [], everything: true } }]),
  );
});

it("draws a running job's areas from its own progress", () => {
  const job = LearnJobSchema.parse({
    ...JOB,
    state: "running",
    progress: {
      at: Date.now() / 1000,
      stage: "areas",
      areas: [
        { id: "root", files: 3, state: "running", calls: 1, tokens: 10 },
        { id: "web", files: 1, state: "listed", calls: 0, tokens: 0 },
      ],
    },
  });
  render(<Learning progress={job.progress} />);
  expect(screen.getByRole("listitem", { name: "root" })).toBeTruthy();
  expect(screen.getByText(/1 not learned/)).toBeTruthy();
});
