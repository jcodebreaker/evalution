// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import {
  ComparisonSchema,
  EstimateSchema,
  RemoteBranchesSchema,
  RunDetailSchema,
  RunSummarySchema,
  VersionsSchema,
} from "../../lib/schema";
import { RunConfirm } from "./RunConfirm";
import { RunRow } from "./RunRow";
import { RunView } from "./RunView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const HEAD = "e535c4d89abb688afc9be53a0dcc3291b2573a52";
const FEATURE = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0";
const COST =
  "Checks out feature-x @ a1b2c3d and builds it: about 4 min and up to 20 GB, once. Your checkout isn't touched.";

const estimateOf = (ref: string) =>
  EstimateSchema.parse({
    token: `t-${ref}`,
    cases: 1,
    tries: 1,
    chats: 1,
    ref,
    version:
      ref === "disk"
        ? {
            branch: "page-testing",
            commit: HEAD,
            dirty: true,
            verified: true,
            said: "what's on disk now: page-testing @ e535c4d, with uncommitted changes",
          }
        : {
            ref,
            branch: ref,
            commit: FEATURE,
            dirty: false,
            said: `${ref} @ a1b2c3d`,
            cost: COST.replace("feature-x", ref),
          },
  });

function open() {
  const estimate = vi
    .spyOn(api, "estimate")
    .mockImplementation(async (_scope, _id, _cases, _tries, ref) => estimateOf(ref ?? "disk"));
  vi.spyOn(api, "versions").mockResolvedValue(
    VersionsSchema.parse({
      target: "relay-lite",
      remotes: ["origin"],
      checked_out: { branch: "page-testing", commit: HEAD },
      refs: [
        { name: "page-testing", ref: "refs/heads/page-testing", kind: "local", commit: HEAD },
        { name: "feature-x", ref: "refs/heads/feature-x", kind: "local", commit: FEATURE },
        { name: "v1.2", ref: "refs/tags/v1.2", kind: "tag", commit: HEAD },
      ],
    }),
  );
  const remote = vi.spyOn(api, "remoteBranches").mockResolvedValue(
    RemoteBranchesSchema.parse({
      remote: "origin",
      branches: [
        { remote: "origin", name: "pushed", ref: "origin/pushed", commit: FEATURE },
        { remote: "origin", name: "main", ref: "origin/main", commit: HEAD },
      ],
    }),
  );
  const fetched = vi
    .spyOn(api, "fetchBranch")
    .mockResolvedValue({ remote: "origin", branch: "pushed", commit: FEATURE });
  render(
    <RunConfirm
      target={{ scope: { target: "relay-lite", set: "main" } }}
      onClose={() => null}
      onStarted={() => null}
    />,
  );
  return { estimate, remote, fetched };
}

it("runs what's on disk by default, and lists the versions when the picker opens", async () => {
  const { estimate, remote } = open();
  await screen.findByText("What's on disk now: page-testing @ e535c4d, with uncommitted changes");
  expect(estimate).toHaveBeenLastCalledWith(
    { target: "relay-lite", set: "main" },
    undefined,
    undefined,
    undefined,
    "disk",
  );
  // Nothing is listed until the picker opens: ls-remote is a network call.
  expect(remote).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Run another version"));
  await waitFor(() => expect(remote).toHaveBeenCalledWith("relay-lite"));
  const picker = (await screen.findByLabelText("Code to run")) as HTMLSelectElement;
  await screen.findByText("origin/pushed");
  const labels = [...picker.querySelectorAll("option")].map((o) => o.textContent);
  expect(labels).toEqual([
    "What's on disk now",
    "page-testing (checked out)",
    "feature-x",
    "origin/pushed",
    "origin/main",
    "v1.2",
    "A commit…",
  ]);
  expect(picker.value).toBe("disk");
});

it("opens on a version passed in, with its cost line", async () => {
  const estimate = vi
    .spyOn(api, "estimate")
    .mockImplementation(async (_scope, _id, _cases, _tries, ref) => estimateOf(ref ?? "disk"));
  render(
    <RunConfirm
      target={{
        scope: { target: "relay-lite", set: "main" },
        ref: "main",
        estimate: estimateOf("disk"),
      }}
      onClose={() => null}
      onStarted={() => null}
    />,
  );
  await screen.findByText("Main @ a1b2c3d");
  expect(screen.getByTestId("version-cost").textContent).toBe(COST.replace("feature-x", "main"));
  expect(estimate.mock.calls.at(-1)?.[4]).toBe("main");
});

it("says the build and its cost before Run when a branch is picked", async () => {
  const { estimate } = open();
  fireEvent.click(await screen.findByText("Run another version"));
  const picker = (await screen.findByLabelText("Code to run")) as HTMLSelectElement;
  await screen.findByText("feature-x");
  fireEvent.change(picker, { target: { value: "local:feature-x" } });
  await screen.findByText("Feature-x @ a1b2c3d");
  expect(screen.getByTestId("version-cost").textContent).toBe(COST);
  expect(estimate).toHaveBeenLastCalledWith(
    { target: "relay-lite", set: "main" },
    undefined,
    undefined,
    undefined,
    "feature-x",
  );
  expect(picker.value).toBe("local:feature-x");
});

it("fetches a remote's branch when it is picked, then estimates it", async () => {
  const { estimate, fetched } = open();
  fireEvent.click(await screen.findByText("Run another version"));
  const picker = (await screen.findByLabelText("Code to run")) as HTMLSelectElement;
  await screen.findByText("origin/pushed");
  fireEvent.change(picker, { target: { value: "remote:origin/pushed" } });
  await screen.findByText("Origin/pushed @ a1b2c3d");
  expect(fetched).toHaveBeenCalledWith("relay-lite", "origin", "pushed");
  expect(estimate.mock.calls.at(-1)?.[4]).toBe("origin/pushed");
});

it("takes a commit typed in", async () => {
  const { estimate } = open();
  fireEvent.click(await screen.findByText("Run another version"));
  const picker = (await screen.findByLabelText("Code to run")) as HTMLSelectElement;
  await screen.findByText("feature-x");
  fireEvent.change(picker, { target: { value: "commit" } });
  fireEvent.change(screen.getByLabelText("Commit"), { target: { value: "a1b2c3d" } });
  fireEvent.click(screen.getByText("Use"));
  await waitFor(() => expect(estimate.mock.calls.at(-1)?.[4]).toBe("a1b2c3d"));
});

it("says a refused version and keeps Run waiting", async () => {
  vi.spyOn(api, "estimate").mockImplementation(async (_s, _i, _c, _t, ref) => {
    if (ref && ref !== "disk") {
      const { ApiError } = await import("../../lib/api");
      throw new ApiError(
        400,
        "Evalution doesn't know how to build relay-lite yet, so it can't build feature-x. Prove how relay-lite is built first, on Test environment.",
      );
    }
    return estimateOf("disk");
  });
  vi.spyOn(api, "versions").mockResolvedValue(
    VersionsSchema.parse({
      target: "relay-lite",
      refs: [{ name: "feature-x", ref: "refs/heads/feature-x", kind: "local", commit: FEATURE }],
    }),
  );
  vi.spyOn(api, "remoteBranches").mockRejectedValue(new Error("offline"));
  render(
    <RunConfirm
      target={{ scope: { target: "relay-lite", set: "main" } }}
      onClose={() => null}
      onStarted={() => null}
    />,
  );
  fireEvent.click(await screen.findByText("Run another version"));
  const picker = (await screen.findByLabelText("Code to run")) as HTMLSelectElement;
  await screen.findByText("feature-x");
  fireEvent.change(picker, { target: { value: "local:feature-x" } });
  await screen.findByText(/Prove how relay-lite is built first, on Test environment\./);
  expect(screen.getByText("Run").closest("button")?.disabled).toBe(true);
});

const RUN = {
  run: "r1",
  target: "relay-lite",
  set: "main",
  started_at: 10,
  status: "done",
  pass_marks: { tools: 0.8, reply: 0.7 },
  totals: { passed: 1, cases: 1, tools: null, reply: null, median_seconds: 0, tokens: 0 },
  version: {
    ref: "feature-x",
    branch: "feature-x",
    commit: FEATURE,
    dirty: false,
    verified: true,
    said: "feature-x @ a1b2c3d",
    notes: [
      "The reset plan was proven on another version (app/db.py differ in feature-x @ a1b2c3d): rehearsed again on this version's copy before the first try, and it held.",
    ],
  },
};

it("chips a run of a version with its branch and commit, and says its notes", () => {
  render(<RunRow run={RunSummarySchema.parse(RUN)} label="Run r1" onOpen={() => {}} />);
  expect(screen.getByTestId("version-chip").textContent).toBe("feature-x @ a1b2c3d");
  cleanup();
  vi.spyOn(api, "comparison").mockResolvedValue(ComparisonSchema.parse({ run: "r1", base: null }));
  renderRun(RUN);
  const details = within(screen.getByTestId("run-details"));
  expect(details.getByText("Code")).toBeTruthy();
  expect(details.getByText("Feature-x @ a1b2c3d")).toBeTruthy();
  expect(screen.getByText(RUN.version.notes[0] as string).className).toContain("text-warning");
});

it("says why a run of a version that wasn't built played nothing, with the build's last lines", () => {
  vi.spyOn(api, "comparison").mockResolvedValue(ComparisonSchema.parse({ run: "r1", base: null }));
  renderRun({
    ...RUN,
    status: "failed",
    failure: "version_not_built",
    stop_reason:
      "feature-x @ a1b2c3d wasn't built. The build stopped:\n#7 RUN uv sync\nerror: no such package",
    version: { ...RUN.version, verified: null, notes: [] },
  });
  const said = screen.getByTestId("version-not-built");
  expect(said.textContent).toContain(
    "Nothing played, so nothing was judged: feature-x @ a1b2c3d wasn't built. The build stopped:",
  );
  expect(said.querySelector("pre")?.textContent).toBe("#7 RUN uv sync\nerror: no such package");
});

function renderRun(run: unknown) {
  const parsed = RunDetailSchema.parse({ ...(run as object), cases: [] });
  render(
    <RunView
      load={{ status: "ready", data: parsed }}
      runs={[]}
      label={(id) => `Run ${id}`}
      scopeLabel="All tests"
      going={false}
      onBack={() => {}}
      onBase={() => {}}
      onTest={() => {}}
      onRunTest={() => {}}
      onRunMore={() => {}}
      onStop={async () => {}}
      onAnalyze={async () => {}}
    />,
  );
}
