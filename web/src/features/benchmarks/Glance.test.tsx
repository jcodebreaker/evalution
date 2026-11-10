// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BenchmarksSchema } from "../../lib/schema";
import { Glance } from "./Glance";

afterEach(cleanup);

const MEMORY = { measured: 0, written: 0, committed: 0, used: 0 };

function run(report: Record<string, unknown>) {
  const [made] = BenchmarksSchema.parse({
    sizes: [],
    runs: [
      {
        run: "r",
        set: "longmemeval-quick",
        status: "done",
        started_at: null,
        judge: "m",
        questions: [],
        report: {
          accuracy: 0.95,
          right: 19,
          judged: 20,
          task_average: null,
          kinds: {},
          abstention: null,
          admitted: 0,
          unanswerable: 0,
          unjudged: 0,
          waiting: 0,
          write_recall: null,
          memory: MEMORY,
          range: [0.764, 0.991],
          ...report,
        },
      },
    ],
  }).runs;
  if (!made) throw new Error("no run");
  return made;
}

function glance(report: Record<string, unknown>) {
  render(<Glance run={run(report)} onStopped={() => {}} />);
}

describe("Glance", () => {
  it("shows accuracy's range and says the numbers are a sample", () => {
    glance({});
    expect(screen.getByText("95% range 76–99%")).toBeTruthy();
    expect(screen.getByText(/not comparable with published scores/)).toBeTruthy();
  });

  it("shows a change in points only when it is real", () => {
    glance({ change_is_real: false, previous_accuracy: 0.9 });
    expect(screen.queryByText(/pts/)).toBeNull();
    cleanup();
    glance({ change_is_real: true, previous_accuracy: 0.25 });
    expect(screen.getByText(/70 pts/)).toBeTruthy();
  });

  it("says how many the app broke off on, counted as wrong", () => {
    glance({ broke_off: 2 });
    expect(screen.getByText("2 timed out, counted as wrong")).toBeTruthy();
    cleanup();
    glance({ broke_off: 0 });
    expect(screen.queryByText(/counted as wrong/)).toBeNull();
  });
});
