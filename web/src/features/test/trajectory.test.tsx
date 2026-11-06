// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CompareSchema } from "../../lib/schema";
import { Trajectory } from "./Trajectory";

afterEach(cleanup);

const LANES = {
  expected: ["Search", "Run code", "Reply"],
  tries: [
    {
      n: 1,
      outcome: "passed",
      steps: [
        { label: "Search", status: "ok", span: "t1-0" },
        { label: "Run code", status: "ok", span: "t1-1" },
        { label: "Reply", status: "ok" },
      ],
    },
    {
      n: 2,
      outcome: "failed",
      steps: [
        { label: "Search", status: "ok", span: "t2-0" },
        { label: "Run code", status: "missing" },
        { label: "Files read", status: "extra", span: "t2-1" },
        { label: "Reply", status: "wrong" },
      ],
    },
  ],
};

function shown(divergence: unknown) {
  return CompareSchema.parse({ name: "Totals", run: "r2", try: 2, trajectory: LANES, divergence });
}

describe("Trajectory", () => {
  it("marks where the failing try first differs, and leads to the passing try there", () => {
    const onPassing = vi.fn();
    const divergence = {
      pass: { run: "r1", n: 1, span: "t1-1" },
      fail: { run: "r2", n: 2, span: "t2-1" },
      step: 1,
      kind: "tool",
      detail: "files_read instead of run_code",
    };
    render(
      <Trajectory
        shown={shown(divergence)}
        runLabel={(run) => `Run ${run}`}
        onPassing={onPassing}
      />,
    );
    expect(screen.getByTitle("Differs from passing try").textContent).toBe("Files read");
    fireEvent.click(screen.getByRole("button", { name: "Differs from passing try 1 (Run r1)" }));
    expect(onPassing).toHaveBeenCalledWith("r1", 1, "t1-1");
    expect(screen.getByText(/files_read instead of run_code/)).toBeTruthy();
  });

  it("marks nothing without a first difference", () => {
    render(<Trajectory shown={shown(null)} runLabel={(run) => run} onPassing={vi.fn()} />);
    expect(screen.queryByTitle("Differs from passing try")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
