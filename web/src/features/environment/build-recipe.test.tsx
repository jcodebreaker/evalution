// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BuildRecipeCard, buildTime, sizeWords } from "./BuildRecipe";

type Call = { method: string; path: string; body: unknown };
let calls: Call[] = [];

/** Answers each `METHOD /path` from `routes` (a function is called each time); else `{}`. */
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const T = "/v1/targets/relay-lite/builds";
const COST = "builds relay-lite's code once: about 4 min and up to 20 GB";
const RECIPE = {
  kind: "compose_env",
  words:
    "Built with docker compose: engine and host (profile build), named through IMAGE_REGISTRY and IMAGE_TAG: evalution/relay-lite/engine:<commit> and evalution/relay-lite/host:<commit>. Your own images aren't touched.",
  services: ["engine", "host"],
  profiles: ["build"],
  name_var: "IMAGE_REGISTRY",
  tag_var: "IMAGE_TAG",
  evidence: [
    {
      at: "deployment/compose.yaml:42",
      says: "engine is named through IMAGE_REGISTRY and IMAGE_TAG",
    },
  ],
};
const build = (extra: Record<string, unknown>) => ({
  target: "relay-lite",
  status: "none",
  recipe: null,
  can_propose: true,
  can_prove: false,
  cost: COST,
  free_bytes: 60e9,
  min_free_bytes: 5 * 1024 ** 3,
  ...extra,
});
const step = (key: string, label: string, state: string) => ({ key, label, state });

it("says sizes and times in words", () => {
  expect(sizeWords(19_500_000_000)).toBe("19.5 GB");
  expect(buildTime(233)).toBe("3 min 53 s");
  expect(buildTime(45)).toBe("45 s");
});

it("proposes a recipe by rule and says it in plain words with what it rests on", async () => {
  serve({
    [`GET ${T}`]: build({}),
    [`POST ${T}/propose`]: build({
      status: "proposed",
      recipe: RECIPE,
      can_prove: true,
      can_undo: true,
    }),
  });
  render(<BuildRecipeCard target="relay-lite" />);
  const propose = await screen.findByRole("button", { name: "Propose" });
  expect(screen.getByText(/your images are never touched/)).toBeTruthy();
  fireEvent.click(propose);
  await screen.findByTestId("build-words");
  expect(calls.at(-1)).toMatchObject({ method: "POST", path: `${T}/propose` });
  expect(screen.getByTestId("build-words").textContent).toContain(
    "evalution/relay-lite/engine:<commit>",
  );
  expect(screen.getByText("Proposed")).toBeTruthy();
  const rests = screen.getByRole("list", { name: "What it rests on" });
  expect(rests.textContent).toContain("deployment/compose.yaml:42");
  // The cost is said before Prove is pressed.
  expect(screen.getByTestId("build-cost").textContent).toBe(`Proving ${COST}.`);
  expect(screen.getByRole("button", { name: "Prove it" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Propose again" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Undo the last recipe" })).toBeTruthy();
});

it("proves it as a job with the build's last lines, then says what the build took", async () => {
  const proposed = build({ status: "proposed", recipe: RECIPE, can_prove: true, can_undo: true });
  const running = build({
    status: "proposed",
    recipe: RECIPE,
    job: {
      state: "running",
      steps: [
        step("build", "Build relay-lite's code into evalution's names", "running"),
        step("copy", "Start a copy from it", "waiting"),
      ],
      lines: ["#5 [engine 3/9] RUN uv sync", "#6 [host 2/7] COPY . ."],
    },
  });
  const proven = build({
    status: "proven",
    recipe: RECIPE,
    can_prove: true,
    can_undo: true,
    last_build: { tag: "disk-1", seconds: 233, disk_bytes: 19_500_000_000 },
    built: [{ tag: "disk-1", images: [], seconds: 233, disk_bytes: 19_500_000_000 }],
    job: { state: "done", steps: [step("build", "Build", "done")], lines: [] },
  });
  let now = proposed;
  serve({
    [`GET ${T}`]: () => now,
    [`POST ${T}/prove`]: () => {
      now = running;
      return running;
    },
    [`POST ${T}/remove`]: build({
      status: "proven",
      recipe: RECIPE,
      removed: {
        images: ["evalution/relay-lite/engine:disk-1"],
        note: "Trimmed Colima's disk",
      },
    }),
  });
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  render(<BuildRecipeCard target="relay-lite" />);
  fireEvent.click(await screen.findByRole("button", { name: "Prove it" }));
  await screen.findByRole("log", { name: "What the build says" });
  expect(calls.at(-1)).toMatchObject({ method: "POST", body: { confirm: true } });
  expect(screen.getByRole("log").textContent).toContain("RUN uv sync");
  expect(screen.getByRole("list", { name: "Steps" }).textContent).toContain(
    "Build relay-lite's code into evalution's names",
  );
  // Nothing to press while it builds.
  expect(screen.queryByRole("button", { name: /Prove/ })).toBeNull();
  now = proven;
  await waitFor(() => expect(screen.getByText("Proven")).toBeTruthy(), { timeout: 3000 });
  expect(screen.getByTestId("last-build").textContent).toBe(
    "Last built in 3 min 53 s, taking 19.5 GB of this computer's disk.",
  );
  expect(screen.getByRole("button", { name: "Prove it again" })).toBeTruthy();
  const versions = screen.getByRole("list", { name: "Built versions" });
  expect(versions.textContent).toContain("disk-1");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  });
  expect(calls.at(-1)).toMatchObject({ path: `${T}/remove`, body: { tag: "disk-1" } });
  await screen.findByText(/Trimmed Colima's disk/);
});

it("says when it is out of date, and when the disk is too full to build", async () => {
  serve({
    [`GET ${T}`]: build({
      status: "stale",
      recipe: RECIPE,
      stale_files: ["deployment/engine.Dockerfile"],
      can_prove: false,
      free_bytes: 3e9,
    }),
  });
  render(<BuildRecipeCard target="relay-lite" />);
  await screen.findByText(/Out of date: deployment\/engine.Dockerfile changed/);
  expect(screen.getByTestId("low-disk").textContent).toContain("Only 3.0 GB is free");
  expect(screen.getByRole("button", { name: "Prove it again" }).hasAttribute("disabled")).toBe(
    true,
  );
});

it("says why a proof failed, and undoes the last recipe", async () => {
  serve({
    [`GET ${T}`]: build({
      status: "failed",
      recipe: RECIPE,
      can_prove: true,
      can_undo: true,
      job: { state: "done", steps: [], lines: [], error: "the copy didn't answer" },
    }),
    [`POST ${T}/undo`]: build({}),
  });
  render(<BuildRecipeCard target="relay-lite" />);
  await screen.findByText("Not proven");
  expect(screen.getByText("the copy didn't answer")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Undo the last recipe" }));
  await screen.findByRole("button", { name: "Propose" });
  expect(calls.at(-1)).toMatchObject({ method: "POST", path: `${T}/undo` });
});
