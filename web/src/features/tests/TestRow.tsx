import { cn } from "../../lib/cn";
import { seconds } from "../../lib/format";
import type { CaseRow, Verdict } from "../../lib/schema";
import { originallyWord, PASSED_MODEL_ONLY, unjudgedWords } from "../../lib/verdict";
import { Cells, Dot, PlayButton } from "../shared/kit";
import { PlaysChip, StaleChip } from "../shared/marks";

/** The columns of the tests table, head and rows alike. */
export const TEST_COLUMNS =
  "grid grid-cols-[minmax(0,1fr)_88px_150px_56px_64px_34px] items-center gap-x-4 max-lg:grid-cols-[minmax(0,1fr)_56px_34px]";
/** A column hidden on narrow screens. */
export const WIDE_ONLY = "max-lg:hidden";

/** The line under a test's name: why it couldn't be judged, its note, else what it is about. */
export function rowLine(row: CaseRow): string | null {
  if (row.verdict === "unjudged") return unjudgedWords(row.unjudged);
  if (row.note) return row.note;
  if (row.verdict === "pass" && row.basis === "model_only") return PASSED_MODEL_ONLY;
  return row.about;
}

/**
 * One test as a row of the table: its verdict dot (flaky is half and half), name and what it is
 * about, how it plays, its last ten runs, its tries and time, and its run button, always there.
 */
export function TestRow({
  row,
  going,
  history,
  onOpen,
  onRun,
}: {
  row: CaseRow;
  going: boolean;
  /** Its verdict in each of the last runs, oldest first; `null` where it didn't play. */
  history?: readonly (Verdict | null)[];
  onOpen: () => void;
  onRun: () => void;
}) {
  const line = rowLine(row);
  const passed = row.tries.filter((outcome) => outcome === "passed").length;
  return (
    <div
      className={cn(
        TEST_COLUMNS,
        "border-rule pe-5 transition-colors not-first:border-t hover:bg-sunk",
      )}
      data-testid="test-row"
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 items-center gap-3 py-3.5 ps-5 text-start focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint"
      >
        <Dot verdict={row.verdict} />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate font-medium">{row.name}</span>
            {row.stale ? <StaleChip reason={row.stale} /> : null}
            {row.different ? (
              <span className="shrink-0 text-[13px] text-ink-3">Different route</span>
            ) : null}
            {row.originally ? (
              <span className="shrink-0 text-[13px] text-ink-3">
                {originallyWord(row.originally)}
              </span>
            ) : null}
          </span>
          {line ? (
            <span title={line} className="truncate text-sm text-ink-3">
              {line}
            </span>
          ) : null}
        </span>
      </button>
      <span className={WIDE_ONLY}>
        <PlaysChip plays={row.plays} />
      </span>
      <span className={WIDE_ONLY}>
        <Cells cells={history ?? []} label={`Last runs of ${row.name}`} />
      </span>
      <span
        className={cn(
          "text-center font-mono text-[13px] tabular-nums",
          row.verdict && row.verdict !== "pass" ? "text-fail" : "text-ink-3",
        )}
      >
        {row.tries.length ? `${passed}/${row.tries.length}` : "–"}
      </span>
      <span className={cn("text-end font-mono text-[13px] text-ink-3 tabular-nums", WIDE_ONLY)}>
        {row.seconds === null ? "–" : seconds(row.seconds, row.seconds < 10)}
      </span>
      <PlayButton label={`Run ${row.name}`} disabled={going} onClick={onRun} />
    </div>
  );
}
