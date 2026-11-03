import { cn } from "../../lib/cn";
import { count, spent, when } from "../../lib/format";
import type { Health } from "../../lib/schema";
import { TextLink } from "../shared/group";
import { VERDICT_CELL } from "../shared/marks";
import { CARD } from "./card";

/** The latest runs, each with a square per test. */
export function RecentRuns({
  recent,
  runLabel,
  onRun,
  onRuns,
}: {
  recent: Health["recent"];
  runLabel: (run: string) => string;
  onRun: (run: string) => void;
  onRuns: () => void;
}) {
  return (
    <div className={cn(CARD, "flex flex-col gap-1")}>
      <div className="mb-2 flex items-baseline gap-2">
        <h2 className="m-0 text-[17px] font-bold">Recent runs</h2>
        <TextLink
          className="ms-auto text-[13px] font-semibold text-tint no-underline"
          onClick={onRuns}
        >
          All runs
        </TextLink>
      </div>
      {recent.map((run) => {
        const passed = run.results.filter((verdict) => verdict === "pass").length;
        return (
          <button
            key={run.run}
            type="button"
            onClick={() => onRun(run.run)}
            className="-mx-3 grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3.5 rounded-[10px] px-3 py-[11px] text-start hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate text-[14px] font-semibold">
                {runLabel(run.run)} · {count(run.results.length, "test")}
              </span>
              <span className="text-[12.5px] text-ink-2">
                {[when(run.at), spent(run.usd)].filter(Boolean).join(" · ")}
              </span>
            </span>
            <span className="flex max-w-40 flex-wrap gap-[3px]" aria-hidden>
              {run.results.map((verdict, at) => (
                <span key={at} className={cn("size-[9px] rounded-[3px]", VERDICT_CELL[verdict])} />
              ))}
            </span>
            <span className="min-w-10 text-end text-[14px] font-bold tabular-nums">
              {passed}/{run.results.length}
            </span>
          </button>
        );
      })}
    </div>
  );
}
