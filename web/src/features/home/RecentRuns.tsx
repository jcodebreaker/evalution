import { live, seconds, tokens, when } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { StatusDot } from "../../ui/status";
import { VersionChip } from "../runs/Version";
import { PANEL } from "./card";
import { didntStart, RECENT, runOutcome, runReason } from "./overview";

const FAILED = new Set(["fail", "flaky"]);

/** "Recent runs": each run's outcome and why in a line, its result, its code, its numbers. */
export function RecentRuns({
  runs,
  numbers,
  onRun,
  onRuns,
}: {
  runs: readonly RunSummary[];
  numbers: ReadonlyMap<string, number>;
  onRun: (run: string) => void;
  onRuns: () => void;
}) {
  if (!runs.length) return null;
  const shown = runs.slice(0, RECENT);
  const th = "bg-sunk px-5 py-2 text-[12px] font-medium whitespace-nowrap text-ink-3";
  return (
    <section className={PANEL} aria-labelledby="recent-runs" data-testid="recent-runs">
      <div className="flex items-baseline gap-3 px-5 pt-4 pb-3">
        <h2 id="recent-runs" className="m-0 text-[16px] font-semibold">
          Recent runs
        </h2>
        <Button variant="link" size="sm" className="ms-auto" onClick={onRuns}>
          {runs.length === 1 ? "The run →" : `All ${runs.length} runs →`}
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr className="border-y border-separator text-start">
              <th className={`${th} text-start`}>Run</th>
              <th className={`${th} text-start`}>Result</th>
              <th className={`${th} text-start max-md:hidden`}>Code</th>
              <th className={`${th} text-end`}>Typical try</th>
              <th className={`${th} text-end max-md:hidden`}>Tokens</th>
              <th className={`${th} text-end`}>Started</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((run) => {
              const label = `Run ${numbers.get(run.run) ?? run.run}`;
              const tried = !live(run) && run.progress.done > 0;
              return (
                <tr
                  key={run.run}
                  className="border-separator not-last:border-b hover:bg-sunk"
                  data-testid="recent-run"
                >
                  <td className="px-5 py-3 align-middle">
                    <div className="flex min-w-0 items-baseline gap-3">
                      <StatusDot state={runOutcome(run)} className="-translate-y-px" />
                      <span className="flex min-w-0 flex-col">
                        <button
                          type="button"
                          onClick={() => onRun(run.run)}
                          className="w-fit text-start font-medium hover:underline focus-visible:outline-2 focus-visible:outline-tint"
                        >
                          {label}
                        </button>
                        <small className="max-w-[52ch] truncate text-[12px] text-ink-3">
                          {runReason(run)}
                        </small>
                      </span>
                    </div>
                  </td>
                  <td className="px-5 py-3 align-middle">
                    <ResultBar run={run} />
                  </td>
                  <td className="px-5 py-3 align-middle max-md:hidden">
                    <VersionChip version={run.version} />
                  </td>
                  <td className="px-5 py-3 text-end align-middle font-mono text-[13px] tabular-nums">
                    {tried ? seconds(run.totals.median_seconds) : "—"}
                  </td>
                  <td className="px-5 py-3 text-end align-middle font-mono text-[13px] text-ink-3 tabular-nums max-md:hidden">
                    {live(run) ? "—" : tokens(run.totals.tokens)}
                  </td>
                  <td className="px-5 py-3 text-end align-middle font-mono text-[13px] whitespace-nowrap text-ink-3 tabular-nums">
                    {when(run.started_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** A run's tests as one bar: passed, failed, couldn't judge; then "n of N". */
function ResultBar({ run }: { run: RunSummary }) {
  const total = run.tests.length || run.totals.cases;
  const passed = run.tests.filter((test) => test.verdict === "pass").length;
  const failed = run.tests.filter((test) => FAILED.has(test.verdict ?? "")).length;
  const unjudged = run.tests.filter((test) => test.verdict === "unjudged").length;
  const share = (n: number) => `${total ? (n / total) * 100 : 0}%`;
  return (
    <div className="flex flex-col gap-1">
      <div
        className="flex h-1.5 w-[88px] overflow-hidden rounded-[3px] bg-sunk-2"
        role="img"
        aria-label={`${passed} passed, ${failed} failed, ${unjudged} couldn't be judged, of ${total}`}
      >
        <i className="h-full bg-pass" style={{ width: share(passed) }} />
        <i className="h-full bg-fail" style={{ width: share(failed) }} />
        <i className="h-full bg-unjudged" style={{ width: share(unjudged) }} />
      </div>
      <small className="font-mono text-[12px] text-ink-3 tabular-nums">
        {didntStart(run) ? "didn't start" : `${run.totals.passed} of ${run.totals.cases}`}
      </small>
    </div>
  );
}
