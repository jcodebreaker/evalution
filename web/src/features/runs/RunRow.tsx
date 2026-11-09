import { cn } from "../../lib/cn";
import { live, seconds, tokens, when } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Spinner } from "../../ui/loading";
import { StatusDot } from "../../ui/status";
import { parts, runLine, runState, tally } from "./outcome";
import { PartsBar } from "./parts";
import { VersionChip } from "./Version";

/** The runs list's columns: run, result, code, typical try, tokens, started. Code and tokens
 * drop out where the list is narrow (it sits in an `@container`). */
export const RUN_COLUMNS =
  "grid grid-cols-[minmax(0,1fr)_96px_72px_112px] items-center gap-x-4 @min-[720px]:grid-cols-[minmax(0,1fr)_104px_minmax(0,150px)_72px_56px_112px]";

/** The runs list's heading row. */
export function RunColumns() {
  return (
    <div
      aria-hidden
      className={cn(
        RUN_COLUMNS,
        "border-b border-separator bg-sunk px-5 py-2 text-[12px] font-medium whitespace-nowrap text-ink-3",
      )}
    >
      <span>Run</span>
      <span>Result</span>
      <span className="@max-[719px]:hidden">Code</span>
      <span className="text-end">Typical try</span>
      <span className="text-end @max-[719px]:hidden">Tokens</span>
      <span className="text-end">Started</span>
    </div>
  );
}

/** A run in the list: its dot, "Run n" and why in a line; a small bar of how its tests came
 * out; the code it played; its typical try, tokens, and when it started. */
export function RunRow({
  run,
  label,
  onOpen,
}: {
  run: RunSummary;
  label: string;
  onOpen: () => void;
}) {
  const found = tally(
    run.totals,
    run.tests.map((test) => test.verdict),
  );
  const state = runState(run, found);
  const going = live(run);
  const line = runLine(run, found);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        RUN_COLUMNS,
        "w-full border-separator px-5 py-3 text-start not-first:border-t hover:bg-sunk focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint",
      )}
    >
      <span className="flex min-w-0 items-baseline gap-3">
        {going ? (
          <Spinner />
        ) : (
          <StatusDot state={state.dot} label={state.word} className="-translate-y-px" />
        )}
        <span className="flex min-w-0 flex-col">
          <b className="font-medium">{label}</b>
          <small title={line} className="truncate text-[12px] text-ink-3">
            {line}
          </small>
        </span>
      </span>
      <span className="flex min-w-0 flex-col gap-1">
        <PartsBar
          parts={parts(found)}
          total={found.total}
          className="h-1.5 w-[88px] rounded-[3px]"
        />
        <small className="font-mono text-[11px] text-ink-3 tabular-nums">{`${found.passed} of ${found.total}`}</small>
      </span>
      <span className="min-w-0 @max-[719px]:hidden">
        <VersionChip version={run.version} />
      </span>
      <span className="text-end font-mono text-[13px] tabular-nums">
        {going || !run.totals.median_seconds ? (
          <span className="text-ink-3">–</span>
        ) : (
          seconds(run.totals.median_seconds)
        )}
      </span>
      <span className="text-end font-mono text-[13px] text-ink-3 tabular-nums @max-[719px]:hidden">
        {tokens(run.totals.tokens)}
      </span>
      <span className="text-end text-[13px] whitespace-nowrap text-ink-3 tabular-nums">
        {when(run.started_at)}
      </span>
    </button>
  );
}
