import type { Place } from "../../lib/router";

/**
 * What is on screen, as the assistant's composer names it in its chip and tells the test writer.
 * `key` changes when the screen does; `run` is the run it is about, which the writer's API carries.
 */
export type ScreenContext = {
  key: string;
  /** The chip's words. */
  label: string;
  /** The same, as plain words put before the question. */
  words: string;
  run: string | null;
};

/** What `place` shows, or null where there is nothing to ask about (Connect, no app yet). */
export function screenContext(
  place: Place,
  {
    testName,
    runLabel,
    testRun,
    benchmarkRun,
  }: {
    testName: (id: string) => string;
    runLabel: (run: string) => string;
    /** The run whose tries the open test shows. */
    testRun: string | null;
    benchmarkRun: (run: string) => boolean;
  },
): ScreenContext | null {
  const target = place.target ?? "the app";
  const at = (label: string, words: string, run: string | null = null) => ({
    key: [place.view, place.case ?? "", run ?? "", label].join("|"),
    label,
    words,
    run,
  });
  if (place.case) {
    const name = testName(place.case);
    const run = testRun;
    if (!run) return at(name, `the test "${name}" (${place.case})`);
    const runName = benchmarkRun(run) ? `benchmark run ${run}` : runLabel(run);
    return at(
      `${name} · ${runName}`,
      `the test "${name}" (${place.case}) and its tries in ${runName} (run ${run})`,
      run,
    );
  }
  if ((place.view === "runs" || place.view === "benchmarks") && place.run) {
    const name = benchmarkRun(place.run) ? `Benchmark run ${place.run}` : runLabel(place.run);
    return at(name, `${name} (run ${place.run})`, place.run);
  }
  switch (place.view) {
    case "home":
      return at("Overview", `the overview of ${target}: its tests and latest runs`);
    case "tests":
      return at("Tests", `the tests of ${target}`);
    case "runs":
      return at("Runs", `the runs of ${target}`);
    case "benchmarks":
      return at("Benchmarks", `the benchmarks of ${target}`);
    case "app":
      return at("What it learned", `what evalution learned about ${target}`);
    case "environment":
      return at("Test environment", `the test environment of ${target}`);
    case "model":
      return at("Settings", "the model settings");
    default:
      return null;
  }
}

const ON_SCREEN = /^On screen: ([^\n]+)\.\n\n/;
const ABOUT_BENCHMARK_RUN = /^About benchmark run \S+, reading its traces: /;

/** The question as sent: what is on screen, in plain words, before what the person wrote. */
export function withContext(text: string, context: ScreenContext | null): string {
  return context ? `On screen: ${context.words}.\n\n${text}` : text;
}

/** What the person wrote, and what it was about when the page added that before it. */
export function askedText(text: string): { asked: string; about: string | null } {
  const screen = ON_SCREEN.exec(text);
  if (screen) return { asked: text.slice(screen[0].length), about: screen[1] ?? null };
  const asked = text.replace(ABOUT_BENCHMARK_RUN, "");
  return { asked, about: asked === text ? null : "this run" };
}
