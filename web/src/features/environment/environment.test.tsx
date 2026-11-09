// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IsolationSchema, TestSetupSchema } from "../../lib/schema";
import { Sidebar } from "../shell/Sidebar";
import { TestEnvironment } from "./TestEnvironment";

type Answer = { status?: number; body?: unknown };
type Call = { method: string; path: string; body: unknown };

let calls: Call[] = [];

/** Answers each `METHOD /path` from `routes`; anything else is an empty object. */
function serve(routes: Record<string, Answer | (() => Answer)>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[`${method} ${path.split("?")[0]}`];
      const { status = 200, body = {} } = (typeof route === "function" ? route() : route) ?? {};
      return new Response(JSON.stringify(body), {
        status,
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
const STORES = [
  { id: "postgres-atrium", kind: "postgres", name: "atrium", holds: "Conversations" },
  { id: "volume-artifact-data", kind: "volume", name: "artifact-data", holds: "Memory index" },
];
const HEALTH = { body: { isolation: { status: "none", stores: STORES } } };
const SETUP = {
  person: { name: "Alex Rivera", language: "English", timezone: "Europe/Lisbon" },
  supports: { person: ["name", "language", "timezone", "new_every_try"], seed: true },
};

const step = (key: string, label: string, state: string, seconds: number | null = null) => ({
  key,
  label,
  state,
  seconds,
});
const STEPS = (states: string[]) =>
  [
    step("find", "Find where it keeps things", states[0] ?? "waiting", 3),
    step("copy", "Copy relay-lite", states[1] ?? "waiting", 18),
    step("store:postgres-atrium", "Give it its own Postgres", states[2] ?? "waiting", 2),
    step("store:volume-artifact-data", "Give it its own volume", states[3] ?? "waiting", 1),
    step("person", "Make a test person", states[4] ?? "waiting", 1),
    step("prove", "Check it resets cleanly", states[5] ?? "waiting", 41),
  ].map((entry) => (entry.state === "waiting" ? { ...entry, seconds: null } : entry));

function isolation(extra: Record<string, unknown>) {
  return { body: { target: "relay-lite", status: "none", can_run: true, ...extra } };
}

function page(
  found: Answer,
  setup: unknown = SETUP,
  mode: "setup" | "page" = "setup",
  more: Record<string, Answer | (() => Answer)> = {},
) {
  serve({
    [`GET ${T}/isolation`]: found,
    [`GET ${T}/isolation/setup`]: { body: setup },
    [`GET ${T}/health`]: HEALTH,
    ...more,
  });
  const onFirstTest = vi.fn();
  render(<TestEnvironment target="relay-lite" mode={mode} onFirstTest={onFirstTest} />);
  return onFirstTest;
}

const copy = () => screen.getByTestId("test-copy");
const block = (key: string) => within(copy()).queryByTestId(`block-${key}`);
const put = () => calls.filter((call) => call.method === "PUT").map((call) => call.body);

// The builder

it("before anything runs: every step waiting, an empty copy, and one button that starts it", async () => {
  page(isolation({}), SETUP, "setup", {
    [`POST ${T}/isolation/run`]: isolation({
      job: { state: "running", started: 1, steps: STEPS(["running", ...Array(5).fill("waiting")]) },
    }),
  });
  await screen.findByText("Setting up a place to test relay-lite");
  await screen.findByText("Give it its own Postgres");
  const steps = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
  expect(steps.map((item) => item.dataset.state)).toEqual(Array(6).fill("waiting"));
  expect(within(copy()).queryAllByRole("listitem")).toHaveLength(0);
  expect(screen.getByText("eval-relay-lite-0")).toBeTruthy();
  expect(screen.getByText(/two messages to evalution's own copy/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Set up testing" }));
  await waitFor(() =>
    expect(calls).toContainEqual({
      method: "POST",
      path: `${T}/isolation/run`,
      body: { confirm: true },
    }),
  );
  await screen.findByText("The agent is setting this up…");
});

it("while it runs, a block shows for each step under way, and none for steps still waiting", async () => {
  page(
    isolation({
      job: {
        state: "running",
        started: 1,
        steps: STEPS(["done", "done", "running", "waiting", "waiting", "waiting"]),
      },
    }),
  );
  await waitFor(() => expect(block("store:postgres-atrium")).toBeTruthy());
  expect(block("app")?.textContent).toContain("own ports");
  expect(block("traces")?.textContent).toContain("to evalution");
  expect(block("store:postgres-atrium")?.textContent).toContain("New Postgres");
  expect(block("store:postgres-atrium")?.textContent).toContain("conversations · empty");
  expect(block("store:volume-artifact-data")).toBeNull();
  expect(block("person")).toBeNull();
  expect(screen.getByText("18 s")).toBeTruthy();
  expect(copy().dataset.tone).toBe("plain");
  const next = screen.getByRole("button", { name: "Write your first test" }) as HTMLButtonElement;
  expect(next.disabled).toBe(true);
});

it("done and proven: every block checked, the copy green, and on to the first test", async () => {
  const onFirstTest = page(
    isolation({
      status: "proven",
      version: 1,
      plan: { stores: [] },
      job: { state: "done", started: 1, ended: 70, steps: STEPS(Array(6).fill("done")) },
    }),
  );
  await screen.findByText("Ready to test relay-lite");
  await waitFor(() => expect(block("person")?.textContent).toContain("Alex Rivera"));
  expect(block("store:volume-artifact-data")?.textContent).toContain("New memory");
  expect(copy().dataset.tone).toBe("ready");
  expect(within(copy()).getAllByLabelText("Checked")).toHaveLength(5);
  expect(screen.getByText("Starts fresh, proven")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Write your first test" }));
  expect(onFirstTest).toHaveBeenCalledTimes(1);
});

it("a failed step says why in the warning colour, and offers Try again", async () => {
  page(
    isolation({
      job: {
        state: "failed",
        started: 1,
        steps: [
          step("find", "Find where it keeps things", "done", 3),
          { ...step("copy", "Copy relay-lite", "failed", 5), detail: "Port 5432 is taken" },
        ],
        error: null,
      },
    }),
    SETUP,
    "setup",
    { [`POST ${T}/isolation/run`]: isolation({}) },
  );
  const detail = await screen.findByText("Port 5432 is taken");
  expect(detail.className).toContain("text-fail");
  expect(screen.getByLabelText("Failed")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
});

it("settled on its screen: out of date, not isolated, not set up", async () => {
  page(
    isolation({ status: "stale", plan: { stores: [] }, stale_files: ["db/utils.py"] }),
    SETUP,
    "page",
  );
  await screen.findByText("Out of date");
  expect(copy().dataset.tone).toBe("stale");
  expect(screen.getByText(/A commit changed db\/utils\.py/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Prove it again" })).toBeTruthy();
  expect(screen.queryByRole("list", { name: "Steps" })).toBeNull();
  cleanup();
  page(isolation({ status: "failed", plan: { stores: [] } }), SETUP, "page");
  await screen.findByText("Not isolated");
  expect(copy().dataset.tone).toBe("leaky");
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  cleanup();
  page(isolation({}), SETUP, "page");
  await screen.findByText("Not set up");
  expect(screen.getByRole("button", { name: "Set up testing" })).toBeTruthy();
  expect(screen.queryByText("Setting up a place to test relay-lite")).toBeNull();
});

it("proven on its screen: the copy green with its blocks from the plan, and its details", async () => {
  page(isolation({ status: "proven", version: 2, plan: { stores: [] } }), SETUP, "page");
  await waitFor(() => expect(block("store:postgres-atrium")).toBeTruthy());
  expect(copy().dataset.tone).toBe("ready");
  expect(screen.queryByRole("button", { name: "Write your first test" })).toBeNull();
  expect(screen.getByText("Details")).toBeTruthy();
  expect(within(screen.getByTestId("your-app")).getByText("Your database")).toBeTruthy();
});

// The setup panel

it("edits the test person: only the fields the app supports, sending only what changed", async () => {
  page(isolation({}), SETUP, "setup", {
    [`PUT ${T}/isolation/setup`]: {
      body: { ...SETUP, person: { ...SETUP.person, name: "Sam Lee" }, needs_check: true },
    },
  });
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  expect(within(panel).getByText("English · Europe/Lisbon")).toBeTruthy();
  expect(screen.queryByText("Changed. It's checked again before the next run.")).toBeNull();
  fireEvent.click(within(panel).getByRole("button", { name: "Edit" }));
  fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Sam Lee" } });
  expect(within(panel).getByLabelText("Language")).toBeTruthy();
  expect(within(panel).getByLabelText("Time zone")).toBeTruthy();
  fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
  await screen.findByText("Changed. It's checked again before the next run.");
  expect(put()).toEqual([{ person: { name: "Sam Lee" } }]);
  expect(within(panel).getByText("Sam Lee")).toBeTruthy();
});

it("an app that names its own test person: its time zone only, and no made-up name", async () => {
  const setup = {
    person: { name: "", timezone: "Europe/Lisbon" },
    supports: { person: ["timezone"], seed: false },
  };
  page(
    isolation({
      job: {
        state: "running",
        started: 1,
        steps: STEPS(["done", "done", "done", "done", "running", "waiting"]),
      },
    }),
    setup,
  );
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  expect(within(panel).getByText("relay-lite names its own test person")).toBeTruthy();
  expect(within(panel).getByText("Europe/Lisbon")).toBeTruthy();
  await waitFor(() => expect(block("person")).toBeTruthy());
  expect(block("person")?.textContent).toContain("Test person");
  expect(block("person")?.textContent).toContain("relay-lite names its own · Europe/Lisbon");
});

it("the time zone is the one field to edit when it's all the app supports", async () => {
  const setup = { person: { timezone: "Europe/Lisbon" }, supports: { person: ["timezone"] } };
  page(isolation({}), setup, "setup", {
    [`PUT ${T}/isolation/setup`]: { body: { ...setup, person: { timezone: "Asia/Tokyo" } } },
  });
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  fireEvent.click(within(panel).getByRole("button", { name: "Edit" }));
  expect(within(panel).queryByLabelText("Name")).toBeNull();
  expect(within(panel).queryByLabelText("Language")).toBeNull();
  fireEvent.change(within(panel).getByLabelText("Time zone"), { target: { value: "Asia/Tokyo" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(put()).toEqual([{ person: { timezone: "Asia/Tokyo" } }]));
});

it("an app with no test person to change says so, with no Edit, and no sample data", async () => {
  page(isolation({}), { supports: { person: [], seed: false } });
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  expect(within(panel).getByText("This app picks its own test person")).toBeTruthy();
  expect(within(panel).queryByRole("button", { name: "Edit" })).toBeNull();
  expect(within(panel).getByRole("radio", { name: /^Nothing/ })).toBeTruthy();
  expect(within(panel).queryByRole("radio", { name: /^Sample data/ })).toBeNull();
});

it("starts from sample data once a file is given", async () => {
  page(isolation({}), SETUP, "setup", {
    [`PUT ${T}/isolation/setup`]: { body: { ...SETUP, start: { kind: "seed", file: "seed.sql" } } },
  });
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  fireEvent.click(within(panel).getByRole("radio", { name: /^Sample data/ }));
  expect(put()).toEqual([]);
  fireEvent.change(within(panel).getByLabelText("Sample data file"), {
    target: { value: "seed.sql" },
  });
  fireEvent.click(within(panel).getByRole("button", { name: "Use" }));
  await waitFor(() => expect(put()).toEqual([{ start: { kind: "seed", file: "seed.sql" } }]));
  fireEvent.click(within(panel).getByRole("radio", { name: /^Before every test/ }));
  await waitFor(() => expect(put()).toContainEqual({ reset: "test" }));
});

it("copies side by side go from 1 to 3", async () => {
  page(isolation({}), { ...SETUP, copies: 1 }, "setup", {
    [`PUT ${T}/isolation/setup`]: { body: { ...SETUP, copies: 3 } },
  });
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  const fewer = within(panel).getByRole("button", { name: "Fewer copies" }) as HTMLButtonElement;
  expect(fewer.disabled).toBe(true);
  fireEvent.click(within(panel).getByRole("button", { name: "More copies" }));
  await waitFor(() => expect(within(panel).getByTestId("copies").textContent).toBe("3"));
  expect(put()).toEqual([{ copies: 2 }]);
  const more = within(panel).getByRole("button", { name: "More copies" }) as HTMLButtonElement;
  expect(more.disabled).toBe(true);
});

it("says a change can't be made while it's being set up", async () => {
  page(isolation({}), SETUP, "setup", {
    [`PUT ${T}/isolation/setup`]: { status: 409, body: {} },
  });
  const panel = await screen.findByRole("complementary", { name: "Test setup" });
  fireEvent.click(within(panel).getByRole("radio", { name: /^Before every test/ }));
  await within(panel).findByText("It's being set up now. Change this when it's done.");
});

// The sidebar, and what the server sends

it("the sidebar opens the Test environment, between What it learned and the tests", () => {
  const onView = vi.fn();
  render(
    <Sidebar
      place={{
        target: "relay-lite",
        set: null,
        view: "environment",
        run: null,
        case: null,
        base: null,
      }}
      targets={[]}
      target="relay-lite"
      sets={[]}
      set={null}
      asking={false}
      onAskAi={null}
      cards={null}
      onTarget={() => {}}
      onSet={() => {}}
      onView={onView}
    />,
  );
  const item = screen.getByRole("button", { name: "Test environment" });
  expect(item.getAttribute("aria-current")).toBe("page");
  const labels = screen.getAllByRole("button").map((button) => button.textContent);
  expect(labels.indexOf("Test environment")).toBe(labels.indexOf("What it learned") + 1);
  fireEvent.click(item);
  expect(onView).toHaveBeenCalledWith("environment");
});

it("reads an older server's steps, and a partial setup, with defaults", () => {
  const old = IsolationSchema.parse({
    target: "bot",
    job: {
      state: "running",
      started: 1,
      steps: [{ key: "find", text: "Finding", state: "doing" }],
    },
  });
  expect(old.job?.steps[0]).toEqual({
    key: "find",
    label: "Finding",
    state: "running",
    seconds: null,
    detail: null,
  });
  const setup = TestSetupSchema.parse({ copies: 2 });
  expect(setup.person.new_every_try).toBe(true);
  expect(setup.start).toEqual({ kind: "empty", file: null });
  expect(setup.reset).toBe("try");
  expect(setup.supports).toEqual({ person: [], seed: false });
  expect(setup.needs_check).toBe(false);
});
