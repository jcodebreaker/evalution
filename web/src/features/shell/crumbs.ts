import type { Place, View } from "../../lib/router";

const SCREEN: Record<View, string> = {
  home: "Overview",
  app: "What it learned",
  environment: "Test environment",
  tests: "Tests",
  benchmarks: "Benchmarks",
  runs: "Runs",
  connect: "Connect an app",
  model: "Settings",
};

/**
 * Where the page is, as the top bar's breadcrumbs: the app, its test set, then the screen and
 * what's open on it (a run, a test). The last is where the person is.
 */
export function crumbsFor(
  place: Place,
  {
    target,
    setName,
    runLabel,
    testName,
  }: {
    target: string | null;
    setName: string | null;
    runLabel: (id: string) => string;
    testName: (id: string) => string;
  },
): string[] {
  if (place.view === "model") return ["Settings"];
  if (place.view === "connect" || !target) return [SCREEN.connect];
  // The app's own screens are the app's, not a test set's.
  const app = place.view === "app" || place.view === "environment" || place.view === "benchmarks";
  const crumbs = [target];
  if (!app && setName) crumbs.push(setName);
  if (place.view === "runs" && place.run) {
    crumbs.push(runLabel(place.run));
    if (place.case) crumbs.push(testName(place.case));
    return crumbs;
  }
  if (place.case && place.view !== "benchmarks") {
    crumbs.push("Tests", testName(place.case));
    return crumbs;
  }
  crumbs.push(SCREEN[place.view]);
  return crumbs;
}
