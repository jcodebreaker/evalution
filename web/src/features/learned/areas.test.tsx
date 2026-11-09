// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  AgentSessionSchema,
  AreaStateSchema,
  LearnedAreaSchema,
  LearnStateSchema,
} from "../../lib/schema";
import { AppView } from "./AppView";
import { areasSummary, LearnedAreas } from "./Areas";
import { Learning } from "./Learning";

const T = "/v1/targets/bot";
const DAY = 86_400;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const area = (id: string, state: string, extra = {}) => ({
  id,
  title: `The ${id} part`,
  state,
  weight: 3,
  files: 4,
  learned_at: null,
  ...extra,
});

const AREAS = [
  area("agents", "done", { weight: 9, files: 12 }),
  area("memory", "current", { weight: 7, files: 5, learned_at: Date.now() / 1000 - 2 * DAY }),
  area("tools", "learning", { weight: 5 }),
  area("routes", "due", { weight: 2 }),
  area("search", "failed", { weight: 1 }),
  area("web", "listed", { weight: 0, files: 1 }),
];

const progress = (extra = {}) =>
  LearnStateSchema.parse({ state: "learning", started_at: 1, areas: AREAS, ...extra });

const claim = (id: string, text: string) => ({
  id,
  text,
  evidence: [{ kind: "code", ref: "app.py" }],
});

/** An understanding: learned, with an older section, two area sections and *extra*. */
const understanding = (extra: Record<string, unknown> = {}) => ({
  version: 2,
  card: { name: "bot", summary: "A bot that answers.", numbers: {} },
  sections: [
    { id: "can_do", title: "What it can do", claims: [claim("c1", "It greets people.")] },
    { id: "area-memory", title: "The memory part", claims: [claim("m1", "It keeps notes.")] },
    { id: "area-agents", title: "The agents part", claims: [claim("a1", "It plans.")] },
  ],
  ...extra,
});

/** Answers the page's reads: the understanding from *next* (called each time), the rest empty. */
function serve(next: () => unknown) {
  const fetch = vi.fn(async (path: string) => {
    const at = path.split("?")[0];
    if (at === `${T}/understanding`) {
      return new Response(JSON.stringify(next()), {
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  return () => fetch.mock.calls.filter(([path]) => path === `${T}/understanding`).length;
}

const page = (props: Partial<Parameters<typeof AppView>[0]> = {}) => (
  <AppView
    target="bot"
    agent={null}
    onLearn={() => undefined}
    onAdd={async () => undefined}
    onSaved={async () => undefined}
    onRemoved={() => undefined}
    {...props}
  />
);

it("lists the areas heaviest first, each marked by its state, under one summary line", () => {
  render(<LearnedAreas progress={progress()} busy={false} onGoOn={() => undefined} />);
  const summary =
    "2 of 6 areas learned · 1 learning · 1 waiting · 1 failed · 1 not learned: nothing in it the app uses";
  expect(screen.getByText(summary)).toBeTruthy();
  const rows = screen.getAllByRole("listitem");
  expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual(
    AREAS.map((entry) => entry.title),
  );
  const mark = (name: string) =>
    within(screen.getByRole("listitem", { name }))
      .getAllByRole("img")[0]
      ?.getAttribute("aria-label");
  expect(mark("The agents part")).toBe("Learned");
  expect(mark("The tools part")).toBe("Learning");
  expect(mark("The routes part")).toBe("Waiting");
  expect(mark("The search part")).toBe("Failed");
  expect(mark("The web part")).toBe("Not learned");
  const agents = screen.getByRole("listitem", { name: "The agents part" });
  expect(agents.textContent).toContain("12 files · weight 9");
  const memory = screen.getByRole("listitem", { name: "The memory part" });
  expect(memory.textContent).toContain("Learned 2 days ago");
  const web = screen.getByRole("listitem", { name: "The web part" });
  expect(web.textContent).toContain("1 file · nothing in it the app uses");
});

it("words the summary for a learn with nothing left", () => {
  const done = [area("a", "done"), area("b", "current")].map((entry) =>
    LearnedAreaSchema.parse(entry),
  );
  expect(areasSummary(done)).toBe("2 of 2 areas learned");
});

it("asks to learn a listed area from its row", async () => {
  const asked = vi.fn(async () =>
    AreaStateSchema.parse({ id: "web", state: "due", weight: 0, files: 1 }),
  );
  render(
    <LearnedAreas
      progress={progress()}
      busy={false}
      onGoOn={() => undefined}
      onLearnArea={asked}
    />,
  );
  const web = screen.getByRole("listitem", { name: "The web part" });
  fireEvent.click(within(web).getByRole("button", { name: "Learn this area" }));
  expect(asked).toHaveBeenCalledWith("web", false);
  await waitFor(() => expect(within(web).getByText("Asked")).toBeTruthy());
  // Only a listed area offers it.
  const routes = screen.getByRole("listitem", { name: "The routes part" });
  expect(within(routes).queryByRole("button")).toBeNull();
});

it("says a stopped learn kept what it learned, and goes on with the learn action", () => {
  const goOn = vi.fn();
  const { rerender } = render(
    <LearnedAreas progress={progress({ state: "stopped" })} busy={false} onGoOn={goOn} />,
  );
  expect(
    screen
      .getByRole("status")
      .textContent?.startsWith("Stopped after 2 of 6 areas; what was learned is kept."),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Go on" }));
  expect(goOn).toHaveBeenCalledTimes(1);
  rerender(<LearnedAreas progress={progress({ state: "done" })} busy={false} onGoOn={goOn} />);
  expect(screen.queryByRole("status")).toBeNull();
});

it("orders the page: card, first-pass overview, areas, what they learned, skeleton", async () => {
  serve(() =>
    understanding({
      progress: { ...progress({ state: "done" }), overview: "first_pass" },
      overview: { state: "first_pass", basis: "the code's skeleton and 12 conversations" },
    }),
  );
  const learn = vi.fn();
  render(page({ onLearn: learn }));
  const pill = await screen.findByText("First pass");
  expect(pill.nextSibling?.textContent).toBe(
    "From the code's skeleton and 12 conversations; it's rewritten once the areas are learned.",
  );
  const overview = screen.getByRole("region", { name: "Overview" });
  expect(within(overview).getByText("A bot that answers.")).toBeTruthy();
  const areas = screen.getByRole("region", { name: "Areas" });
  // The areas' sections show, heaviest area first, after the older ones.
  const headings = screen.getAllByRole("heading").map((heading) => heading.textContent);
  const at = (text: string) => headings.indexOf(text);
  expect(at("What it can do")).toBeLessThan(at("The agents part"));
  expect(at("The agents part")).toBeLessThan(at("The memory part"));
  expect(at("What it is")).toBeLessThan(at("Areas"));
  expect(at("Areas")).toBeLessThan(at("What it can do"));
  // Overview → areas → sections: in the document's order too.
  const follows = (a: Node, b: Node) =>
    Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(follows(overview, areas)).toBe(true);
  expect(follows(areas, screen.getByText("It keeps notes."))).toBe(true);
});

it("drops the first-pass pill once the overview is final", async () => {
  serve(() =>
    understanding({
      progress: { ...progress({ state: "done" }), overview: "final" },
      overview: { state: "final", basis: "the areas" },
    }),
  );
  render(page());
  await screen.findByText("A bot that answers.");
  expect(screen.queryByText("First pass")).toBeNull();
});

it("reads the understanding every 3 s while it says it is learning, and stops after", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  let state = "learning";
  const reads = serve(() => understanding({ progress: progress({ state }) }));
  render(page());
  await screen.findByText(/areas learned/);
  expect(reads()).toBe(1);
  await act(async () => {
    vi.advanceTimersByTime(3_000);
  });
  await waitFor(() => expect(reads()).toBe(2));
  state = "done";
  await act(async () => {
    vi.advanceTimersByTime(3_000);
  });
  await waitFor(() => expect(reads()).toBe(3));
  await act(async () => {
    vi.advanceTimersByTime(9_000);
  });
  expect(reads()).toBe(3);
});

it.each([
  ["an older server", {}],
  ["a learn that kept no progress", { progress: { state: null, areas: [] }, overview: null }],
])("renders as before for %s", async (_, extra) => {
  const reads = serve(() => understanding(extra));
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  render(page());
  await screen.findByText("A bot that answers.");
  expect(screen.queryByRole("region", { name: "Areas" })).toBeNull();
  expect(screen.queryByText("First pass")).toBeNull();
  // An area's section stays the test writer's note.
  expect(screen.queryByText("It keeps notes.")).toBeNull();
  expect(screen.getByText("It greets people.")).toBeTruthy();
  await act(async () => {
    vi.advanceTimersByTime(9_000);
  });
  expect(reads()).toBe(1);
});

it("keeps the Learning card to its stages and time when the page lists the areas", () => {
  const session = AgentSessionSchema.parse({
    id: "a1",
    target: "bot",
    set: "main",
    status: "working",
    messages: [],
    learn: {
      at: Date.now() / 1000,
      stage: "areas",
      parallel: 8,
      areas: [{ id: "agents", files: 3, state: "running" }],
    },
  });
  render(<Learning session={session} areasShown />);
  expect(screen.getAllByRole("listitem").map((stage) => stage.textContent)).toEqual([
    "Skeletondone",
    "Areas0 of 1",
    "Overviewwaiting",
  ]);
  expect(screen.getByText("8 areas at a time")).toBeTruthy();
  expect(screen.queryByText(/running/)).toBeNull();
});
