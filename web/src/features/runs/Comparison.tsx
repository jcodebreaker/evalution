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
  return side ? `${side.passed} of ${side.judged}` : "not run";
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
      <p className="m-0 text-[13px] text-ink-2">No earlier run of this test set to compare with.</p>
    );
  }
  const found = load.data;
  const moved = found.tests.filter((test) => test.change !== "same");
  const same = found.tests.length - moved.length;
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
        <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
          Compared with
          <select
            value={base}
            onChange={(event) => setPicked({ run: run.run, base: event.target.value })}
            className="rounded-md bg-bubble px-1.5 py-1 text-[13px] text-foreground"
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
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-[13px] text-ink-2">
          {found.changed.map((row) => (
            <li key={row.what} className="min-w-0 break-words">
              {`${row.what}: ${row.before} → ${row.now}`}
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 text-[13px] text-ink-2">Nothing recorded about the setup changed.</p>
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
                {WORDS[test.change] ?? test.change}
              </span>
              <TextLink onClick={() => onTest(test.case)}>{test.name}</TextLink>
              <span className="text-[13px] text-ink-2">
                {`${tries(test.before)} → ${tries(test.now)}`}
              </span>
              {test.caveats.length ? (
                <span className="text-[12.5px] text-ink-3">{test.caveats.join(", ")}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {same ? (
        <p className="m-0 text-[13px] text-ink-2">
          {same === 1 ? "1 test didn't change." : `${same} tests didn't change.`}
        </p>
      ) : null}
      {found.could_be_chance ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <p className="m-0 min-w-0 flex-1 text-[13px] text-ink-2">
            This few tries could be the agent varying.
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
