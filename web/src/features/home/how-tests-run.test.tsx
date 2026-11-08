// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CompareSchema, EstimateSchema, HealthSchema, PlanStepSchema } from "../../lib/schema";
import { OpenEnvironment } from "../environment/use-isolation";
import { IsolationLines } from "../runs/RunConfirm";
import { Steps } from "../test/Steps";
import { HowTestsRun } from "./HowTestsRun";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const STORES = [
  {
    id: "postgres-atrium",
    kind: "postgres",
    name: "atrium",
    holds: "Conversations",
    reset: true,
  },
  {
    id: "volume-artifact-data",
    kind: "volume",
    name: "artifact-data",
    holds: "Memory index",
    reset: true,
  },
];

const card = (extra: Record<string, unknown>) =>
  HealthSchema.shape.isolation.parse({ status: "none", ...extra });

const home = (extra: Record<string, unknown>) =>
  render(<HowTestsRun target="relay-lite" isolation={card(extra)} />);

it("Home says a proven plan's stores in plain words, with when it was proven", () => {
  home({
    status: "proven",
    version: 1,
    updated: 1794098664,
    stores: STORES,
    exclude: ["atrium.phoenix.spans"],
  });
  expect(
    screen.getByText(
      "Each try starts clean. evalution runs its own copy of relay-lite and puts it back before every try, so a pass never rests on what an earlier try left behind.",
    ),
  ).toBeTruthy();
  expect(screen.getByText("Conversations (Postgres database atrium)")).toBeTruthy();
  expect(screen.getByText("Memory index (Docker volume artifact-data)")).toBeTruthy();
  expect(screen.getByText("atrium.phoenix.spans")).toBeTruthy();
  expect(screen.getByText(/^Proven .*2026, plan v1$/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Find and prove/ })).toBeNull();
});

it("Home opens the Test environment from Details, and the explainer from How this works", async () => {
  const open = vi.fn();
  render(
    <OpenEnvironment.Provider value={open}>
      <HowTestsRun
        target="relay-lite"
        isolation={card({ status: "proven", version: 1, stores: STORES })}
      />
    </OpenEnvironment.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(open).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "How this works" }));
  await screen.findByText("How isolation works");
  expect(screen.getByText("Find where the app keeps things")).toBeTruthy();
  expect(screen.getByText("What it can't reset")).toBeTruthy();
});

it("the Run dialog's How this works opens the Test environment where the page can move", () => {
  const open = vi.fn();
  render(
    <OpenEnvironment.Provider value={open}>
      <IsolationLines
        app="relay-lite"
        estimate={EstimateSchema.parse({
          token: "t",
          cases: 1,
          tries: 1,
          chats: 1,
          isolation: { how: "proven", plan_version: 1, status: "proven" },
        })}
      />
    </OpenEnvironment.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "How this works" }));
  expect(open).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("How isolation works")).toBeNull();
});

it("Home says what changed under a stale plan, that the next run proves it again, and the button", () => {
  home({ status: "stale", version: 2, stores: STORES, stale_files: ["db/utils.py"] });
  expect(
    screen.getByText(/a commit changed db\/utils\.py\. The next run proves it again first/),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Prove it again now" })).toBeTruthy();
});

it("Home says where a failed plan leaked, and the button", () => {
  home({ status: "failed", version: 1, leaks: ["eval-relay-lite-0-engine-1:/data/cache"] });
  expect(screen.getByText(/Tries aren't isolated yet/)).toBeTruthy();
  expect(screen.getByText("Where it leaked")).toBeTruthy();
  expect(screen.getByText("eval-relay-lite-0-engine-1:/data/cache")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Find and prove it again" })).toBeTruthy();
});

it("Home warns when there is no plan, says what the connection promises, and offers the button", () => {
  home({
    status: "none",
    note: "Each try gets a new session id; what carries over is up to your app.",
  });
  expect(
    screen.getByText(
      "Tries aren't isolated. Each try can see what earlier tries left behind: a pass may rest on leftovers, and a fail may be caused by one.",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "On its own, the connection promises: Each try gets a new session id; what carries over is up to your app.",
    ),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Find and prove isolation" })).toBeTruthy();
  expect(screen.getByText(/two messages to evalution's own copy/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Details" })).toBeNull();
});

it("Home reads an older server's payload, which says only a stale plan's files", () => {
  const old = HealthSchema.shape.isolation.parse({ status: "stale", stale_files: ["a.py"] });
  expect(old?.stores).toEqual([]);
  expect(HealthSchema.shape.isolation.parse(null)).toBeNull();
});

// The try's "Before" step

const BEFORE = [PlanStepSchema.parse({ kind: "before", title: "A fresh person" })];

function before(isolation: Record<string, unknown>) {
  render(
    <Steps
      steps={BEFORE.map((step) => ({ ...step, actual: { ...DONE } }))}
      names={{}}
      appName="relay-lite"
      result
      isolation={CompareSchema.shape.isolation.parse(isolation)}
    />,
  );
}

const DONE = { passed: true, unjudged: false, text: "Done", calls: [], spans: [], quotes: [] };

it("a proven try started clean: what was put back, how long it took, and checked", () => {
  before({
    how: "proven",
    plan_version: 1,
    restore_seconds: 7.14,
    checked: ["postgres-atrium", "volume-artifact-data"],
    stores: STORES,
  });
  expect(screen.getByText("Started clean")).toBeTruthy();
  expect(
    screen.getByText(
      "Put back conversations (Postgres database atrium) and memory index (Docker volume artifact-data) in 7.1 s, and checked them.",
    ),
  ).toBeTruthy();
  expect(screen.queryByText("Done")).toBeNull();
});

it("a try whose putting back didn't hold says so in the warning colour", () => {
  before({
    how: "proven",
    plan_version: 1,
    restore_seconds: 7,
    checked: ["volume-artifact-data"],
    problem: "reset didn't hold: postgres-atrium",
    stores: STORES,
  });
  expect(screen.getByText("Not started clean")).toBeTruthy();
  const warning = screen.getByText(
    "Putting it back didn't hold: conversations (Postgres database atrium). This try isn't judged.",
  );
  expect(warning.className).toContain("text-warning");
});

it("a run recorded with what reset each store on the copy still names them", () => {
  before({
    how: "proven",
    plan_version: 1,
    restore_seconds: 7.14,
    checked: [
      "volume:eval-relay-lite-0_artifact-data",
      "postgres:eval-relay-lite-0-postgres-1/atrium",
    ],
    stores: STORES,
  });
  expect(
    screen.getByText(
      "Put back memory index (Docker volume artifact-data) and conversations (Postgres database atrium) in 7.1 s, and checked them.",
    ),
  ).toBeTruthy();
});

it("an older run's ids show as they are", () => {
  before({ how: "proven", plan_version: 1, restore_seconds: 4, checked: ["a", "b"] });
  expect(screen.getByText("Put back a and b in 4 s, and checked them.")).toBeTruthy();
});

it("a new person only, and not isolated at all", () => {
  before({ how: "identity" });
  expect(screen.getByText("A new person")).toBeTruthy();
  expect(
    screen.getByText(
      "A new person, with nothing of earlier tries'; what the app keeps elsewhere isn't checked.",
    ),
  ).toBeTruthy();
  cleanup();
  before({ how: "none" });
  expect(screen.getByText("Not started clean")).toBeTruthy();
  expect(
    screen.getByText("Not isolated: this try may see what earlier ones left.").className,
  ).toContain("text-warning");
});

// The Run dialog

const estimate = (isolation: Record<string, unknown> | undefined, note?: string) =>
  EstimateSchema.parse({
    token: "t",
    cases: 2,
    tries: 2,
    chats: 2,
    isolation_note: note ?? null,
    ...(isolation ? { isolation } : {}),
  });

it("the Run dialog says what it sends and how a proven plan keeps tries apart", async () => {
  render(
    <IsolationLines
      app="relay-lite"
      estimate={estimate({
        how: "proven",
        plan_version: 1,
        status: "proven",
        start_seconds: 18.2,
        restore_seconds: 7.1,
      })}
    />,
  );
  expect(screen.getByText("2 tries, 2 messages to the app")).toBeTruthy();
  expect(
    screen.getByText(
      "Starts evalution's own copy of relay-lite (about 18 s), puts it back before each try (about 7 s each), and removes it after. Your app isn't touched.",
    ),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "How this works" }));
  await screen.findByText("How isolation works");
});

it("the Run dialog leaves out timings it hasn't seen, and says a stale plan is proven first", () => {
  render(
    <IsolationLines
      app="relay-lite"
      estimate={estimate({ how: "none", status: "stale", rehearse_seconds: 41 })}
    />,
  );
  expect(screen.getByText("Proves the plan again first (about 41 s).")).toBeTruthy();
  expect(
    screen.getByText(
      "Starts evalution's own copy of relay-lite, puts it back before each try, and removes it after. Your app isn't touched.",
    ),
  ).toBeTruthy();
});

it("the Run dialog warns without a plan, and an older estimate keeps its note", () => {
  render(<IsolationLines app="bot" estimate={estimate({ how: "none", status: "none" })} />);
  expect(
    screen.getByText("Not isolated: each try may see what earlier tries left behind.").className,
  ).toContain("text-warning");
  cleanup();
  render(
    <IsolationLines app="bot" estimate={estimate(undefined, "Each try gets a new session id.")} />,
  );
  expect(screen.getByText("Each try gets a new session id.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "How this works" })).toBeNull();
});
