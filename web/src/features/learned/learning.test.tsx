// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentSessionSchema, AreaStateSchema, TurnUsageSchema } from "../../lib/schema";
import { LastLearn } from "./LastLearn";
import { Learning } from "./Learning";

afterEach(cleanup);

const area = (id: string, state: string, extra = {}) => ({
  id,
  files: 3,
  state,
  calls: 0,
  tokens: 0,
  now: "",
  ...extra,
});

const session = (learn: unknown, usage: unknown[] = []) =>
  AgentSessionSchema.parse({
    id: "a1",
    target: "bot",
    set: "main",
    status: "working",
    messages: [],
    learn,
    usage,
  });

const LEARNING = session({
  at: Date.now() / 1000,
  stage: "areas",
  files: 40,
  skeleton: 12,
  parallel: 8,
  areas: [
    area("root", "learned", { weight: 9, steps: 7, calls: 2, tokens: 3000 }),
    area("agents", "running", { weight: 4, steps: 5, calls: 1, now: "read_code agents/a.py" }),
    area("lib", "kept", { weight: 1 }),
    area("web", "listed", { weight: 0 }),
  ],
});

const current = (at: number | null = 1_000) =>
  AreaStateSchema.parse({ id: "web", state: "current", weight: 0, files: 3, learned_at: at });

it("shows Skeleton, then Areas n of m, then Overview, with no global read", () => {
  render(<Learning session={LEARNING} />);
  const stages = screen.getAllByRole("listitem").slice(0, 3);
  expect(stages.map((stage) => stage.textContent)).toEqual([
    "Skeleton12 parts",
    "Areas1 of 2",
    "Overviewwaiting",
  ]);
  expect(screen.queryByText(/Read/)).toBeNull();
  expect(screen.getByText(/1 not learned/)).toBeTruthy();
  // An area's bar has as many segments as its agent may take steps: they grow with its weight.
  const root = screen.getByRole("listitem", { name: "root" });
  expect(root.querySelectorAll("[aria-hidden] > span").length).toBe(7);
  expect(
    within(screen.getByRole("listitem", { name: "web" })).getByText("Not learned"),
  ).toBeTruthy();
});

it("says what it's doing before the areas: the map, then the skeleton", () => {
  const at = Date.now() / 1000;
  render(<Learning session={session({ at, stage: "skeleton", files: 40, areas: [] })} />);
  expect(screen.getByText(/Finding the prompts, tools and state/)).toBeTruthy();
  cleanup();
  render(<Learning session={session({ at, stage: "map", files: 40, areas: [] })} />);
  expect(screen.getByText("Mapping the code")).toBeTruthy();
  expect(screen.getAllByRole("listitem")[0]?.textContent).toBe("Skeleton40 files");
});

it("learns a listed area on request, and asks again only on purpose when it is current", async () => {
  const asked = vi.fn(async (_area: string, force?: boolean) =>
    force ? AreaStateSchema.parse({ id: "web", state: "current" }) : current(),
  );
  render(<Learning session={LEARNING} onLearnArea={asked} />);
  const web = screen.getByRole("listitem", { name: "web" });
  fireEvent.click(within(web).getByRole("button", { name: "Learn this area" }));
  expect(asked).toHaveBeenCalledWith("web", false);
  await waitFor(() => expect(within(web).getByText(/^Current, learned/)).toBeTruthy());
  fireEvent.click(within(web).getByRole("button", { name: "Learn it again anyway" }));
  expect(asked).toHaveBeenLastCalledWith("web", true);
  await waitFor(() => expect(within(web).getByText("Asked")).toBeTruthy());
  // Only a listed area offers it.
  const lib = screen.getByRole("listitem", { name: "lib" });
  expect(within(lib).queryByRole("button")).toBeNull();
});

it("says why asking failed, and lets it be asked again", async () => {
  const asked = vi.fn(async () => {
    throw new Error("The test writer is busy. Try again when it's done.");
  });
  render(<Learning session={LEARNING} onLearnArea={asked} />);
  const web = screen.getByRole("listitem", { name: "web" });
  fireEvent.click(within(web).getByRole("button", { name: "Learn this area" }));
  await waitFor(() => expect(within(web).getByRole("alert").textContent).toMatch(/busy/));
  expect(within(web).getByRole("button", { name: "Learn this area" })).toBeTruthy();
});

it("lists after a learn the areas it didn't learn, each with Learn this area", () => {
  const done = AgentSessionSchema.parse({
    ...LEARNING,
    status: "idle",
    learn: { ...LEARNING.learn, stage: "saving" },
    usage: [
      TurnUsageSchema.parse({
        learn: true,
        at: 10,
        calls: 4,
        stages: [
          { stage: "code map", seconds: 2 },
          { stage: "skeleton", seconds: 1 },
          { stage: "areas", seconds: 40, calls: 3, tokens_in: 9_000, tokens_out: 1_000 },
          { stage: "overview", seconds: 9, calls: 1 },
        ],
      }),
    ],
  });
  const asked = vi.fn(async () => current());
  render(<LastLearn session={done} onLearnArea={asked} />);
  expect(screen.getByText(/Code map 2 s · Skeleton 1 s · Areas 40 s, 3 calls/)).toBeTruthy();
  expect(screen.getByText(/Not learned, nothing in it the app uses/)).toBeTruthy();
  const web = screen.getByRole("listitem", { name: "web" });
  fireEvent.click(within(web).getByRole("button", { name: "Learn this area" }));
  expect(asked).toHaveBeenCalledWith("web", false);
});
