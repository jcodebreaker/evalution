import { useState } from "react";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { Comparison as Compared, RunSummary } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { TextLink } from "../shared/group";

/** Enough tries for a total swing to rule out chance, against the three a test usually has. */
const MORE_TRIES = 5;

type Side = Compared["tests"][number]["before"];

function tries(side: Side): string {
  if (!side || side.verdict === "not_run") return "not run";
  return side.judged ? `${side.passed} of ${side.judged}` : "not judged";
}

/** The tests that didn't move, in a line by how they came out: "4 tests pass, as before". */
export function sameWords(all: readonly Compared["tests"][number][]): string | null {
  const same = all.filter((test) => test.change === "same");
  if (!same.length) return null;
  const n = (verdicts: string[]) =>
    same.filter((test) => verdicts.includes(test.now?.verdict ?? "")).length;
  const pass = n(["pass"]);
  const fail = n(["fail", "flaky"]);
  const rest = same.length - pass - fail;
  const tests = (k: number) => (k === 1 ? "1 test" : `${k} tests`);
  return [
    pass ? `${tests(pass)} ${pass === 1 ? "passes" : "pass"}, as before` : null,
    fail ? `${tests(fail)} still ${fail === 1 ? "fails" : "fail"}` : null,
    rest ? `${tests(rest)} came out the same` : null,
  ]
    .filter(Boolean)
    .join("; ");
}

const WORDS: Record<string, string> = {
  worse: "Worse",
  better: "Better",
  same: "Same",
  unknown: "Couldn't judge",
  only_one: "In one run only",
};

/** Did this change make it worse? This run against an earlier run of its own set: what changed,
 * and which tests moved. */
export function Comparison({
  run,
  runs,
  label,
  going,
  onTest,
  onRunMore,
}: {
  run: Pick<RunSummary, "run" | "target" | "set" | "started_at">;
  runs: RunSummary[];
  label: (run: string) => string;
  going: boolean;
  onTest: (id: string) => void;
  onRunMore: (ids: readonly string[], tries: number) => void;
}) {
  const [chosen, setPicked] = useState<{ run: string; base: string } | null>(null);
  // A base picked on another run's page is not this one's.
  const picked = chosen?.run === run.run ? chosen.base : null;
  const [load] = useLoad(`comparison:${run.run}:${picked ?? ""}`, () =>
    api.comparison(run.run, picked),
  );
  if (load.status !== "ready" || load.data.run !== run.run) return null;
  if (!load.data.base) {
    return (
      <p className="m-0 text-[14px] text-ink-2">No earlier run of this test set to compare with.</p>
    );
  }
  const found = load.data;
  const moved = found.tests.filter((test) => test.change !== "same");
  const same = sameWords(found.tests);
  // Runs of another set or app share none of its tests: every one would be "in one run only".
  const others = runs
    .filter(
      (other) =>
        other.run !== run.run &&
        other.target === run.target &&
        other.set === run.set &&
        other.started_at < run.started_at,
    )
    .sort((a, b) => b.started_at - a.started_at);
  const base = found.base as string;
  return (
    <section className="flex flex-col gap-3 rounded-[14px] bg-group p-4 ring-1 ring-separator">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 className="m-0 min-w-0 flex-1 text-[17px] font-bold text-balance">{found.headline}</h3>
        <label className="flex items-center gap-1.5 text-[14px] text-ink-2">
          Compared with
          <select
            value={base}
            onChange={(event) => setPicked({ run: run.run, base: event.target.value })}
            className="rounded-md bg-bubble px-1.5 py-1 text-[14px] text-foreground"
          >
            {others.some((other) => other.run === base) ? null : (
              <option value={base}>{label(base)}</option>
            )}
            {others.map((other) => (
              <option key={other.run} value={other.run}>
                {label(other.run)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {found.changed.length ? (
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[14px] text-ink-2">
          {found.changed.map((row) => (
            <li key={row.what} className="min-w-0 break-words">
              {`${row.what}: ${row.before} → ${row.now}`}
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 text-[14px] text-ink-2">
          Same setup as that run: no recorded change in model, code, prompt or grader.
        </p>
      )}
      {moved.length ? (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-[14px]">
          {moved.map((test) => (
            <li key={test.case} className="flex flex-wrap items-baseline gap-x-2">
              <span
                className={cn(
                  "font-semibold",
                  test.change === "worse" && "text-destructive",
                  test.change === "better" && "text-tint",
                  test.change !== "worse" && test.change !== "better" && "text-ink-2",
                )}
              >
                {test.change === "unknown" && test.now?.verdict === "not_run"
                  ? "Not run"
                  : (WORDS[test.change] ?? test.change)}
              </span>
              <TextLink onClick={() => onTest(test.case)}>{test.name}</TextLink>
              <span className="text-[14px] text-ink-2">
                {`${tries(test.before)} → ${tries(test.now)}`}
              </span>
              {test.caveats.length ? (
                <span className="text-[13px] text-ink-3">{test.caveats.join(", ")}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {same ? (
        <p className="m-0 flex flex-wrap items-baseline gap-x-2 text-[14px]">
          <span className="font-semibold text-ink-2">Same</span>
          <span className="text-ink-2">{same}</span>
        </p>
      ) : null}
      {found.could_be_chance ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <p className="m-0 min-w-0 flex-1 text-[14px] text-ink-2">
            Too few tries to tell this from the agent varying.
          </p>
          <Button
            size="sm"
            variant="secondary"
            disabled={going}
            onClick={() =>
              onRunMore(
                moved
                  .filter((test) => test.change === "worse" || test.change === "better")
                  .map((test) => test.case),
                MORE_TRIES,
              )
            }
          >
            {`Run with ${MORE_TRIES} tries`}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
