import type { Place, View } from "../../lib/router";

/** Where the top bar's back button leads. */
export type Back = {
  label: string;
  /** A top-level screen to go to; else `to` moves within the open run. */
  list: View | null;
  to: Partial<Place>;
};

/**
 * The top bar's way back, if the screen has one. A top-level screen has none: its own heading is
 * its title. A test goes back to Tests (or to the run it was opened from), a run to Runs, and a
 * benchmark's try to Benchmarks (a benchmark's run is part of that screen).
 */
export function backFor(
  place: Place,
  {
    runLabel,
    benchmarkRun,
  }: {
    runLabel: (run: string) => string;
    benchmarkRun: (run: string) => boolean;
  },
): Back | null {
  if (place.run && benchmarkRun(place.run)) {
    if (!place.case) return null;
    return { label: "Benchmarks", list: null, to: { view: "benchmarks", case: null, set: null } };
  }
  if (place.case && place.view !== "benchmarks") {
    if (place.view === "runs" && place.run) {
      return { label: runLabel(place.run), list: null, to: { case: null } };
    }
    return { label: "Tests", list: "tests", to: {} };
  }
  if (place.view === "runs" && place.run) {
    return { label: "Runs", list: "runs", to: {} };
  }
  return null;
}
