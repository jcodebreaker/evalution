import { cn } from "../../lib/cn";
import { PANEL, PANEL_HEAD } from "./card";
import type { CellState, Grid } from "./overview";

const FILL: Record<CellState, string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  unjudged: "bg-unjudged",
  none: "bg-sunk-2",
  playing: "bg-sunk-2",
  didnt_start:
    "border-separator bg-[repeating-linear-gradient(135deg,var(--sunk-2)_0_4px,var(--group)_4px_8px)]",
};

const CELL = "h-[30px] min-w-0 rounded-[7px] border border-transparent";

/** The latest run's column: a purple ring, apart from the cell by the card's own colour. */
const NOW = "shadow-[0_0_0_2px_var(--group),0_0_0_4px_var(--tint)]";

/** "Each test, run by run": a row per test, a column per run, oldest on the left. */
export function RunGrid({
  grid,
  setName,
  onTest,
  onRun,
  onRuns,
}: {
  grid: Grid;
  /** The set as people name it, when known. */
  setName: string | null;
  onTest: (id: string, run: string) => void;
  onRun: (run: string) => void;
  /** The Runs screen, where every run is listed. */
  onRuns?: () => void;
}) {
  const { columns, rows } = grid;
  if (!rows.length || !columns.length) return null;
  const states = new Set(rows.flatMap((row) => row.cells.map((cell) => cell.state)));
  return (
    <section className={PANEL} aria-labelledby="run-grid" data-testid="run-grid">
      <div className={PANEL_HEAD}>
        <h2 id="run-grid" className="m-0 text-[15px] font-semibold">
          Each test, run by run
        </h2>
        <span className="ms-auto text-[13px] text-ink-3">
          {setName ? `${setName} · ` : ""}last{" "}
          {columns.length === 1 ? "run" : `${columns.length} runs`}
        </span>
        {onRuns ? (
          <button
            type="button"
            onClick={onRuns}
            className="text-[13px] font-medium text-tint-ink hover:underline hover:underline-offset-[3px] focus-visible:outline-2 focus-visible:outline-tint"
          >
            All runs →
          </button>
        ) : null}
      </div>
      <div className="overflow-x-auto px-5 pt-4 pb-5">
        <table
          aria-label="Each test's outcome per run"
          className="w-full min-w-[420px] table-fixed border-separate border-spacing-1 text-[13px]"
        >
          <colgroup>
            <col className="w-[34%]" />
            {columns.map((column) => (
              <col key={column.run} />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th className="p-0 text-start">
                <span className="sr-only">Test</span>
              </th>
              {columns.map((column) => (
                <th
                  key={column.run}
                  scope="col"
                  className={cn(
                    "p-0 text-center font-mono text-xs font-medium text-ink-3",
                    column.latest && "font-semibold text-tint-ink",
                  )}
                >
                  <span className="sr-only">Run </span>
                  {column.n}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.case}>
                <th
                  scope="row"
                  className="truncate p-0 pe-2 text-start font-medium"
                  title={row.name}
                >
                  {row.name}
                </th>
                {row.cells.map((cell, at) => {
                  const latest = columns[at]?.latest ?? false;
                  const className = cn(CELL, FILL[cell.state], latest && NOW);
                  const opens =
                    cell.state === "pass" || cell.state === "fail" || cell.state === "unjudged";
                  return (
                    <td key={cell.run} className="p-0">
                      {opens || cell.state === "didnt_start" ? (
                        <button
                          type="button"
                          title={cell.title}
                          aria-label={cell.title}
                          data-state={cell.state}
                          data-latest={latest || undefined}
                          onClick={() => (opens ? onTest(cell.case, cell.run) : onRun(cell.run))}
                          className={cn(
                            className,
                            "block w-full transition-transform hover:scale-[1.08] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tint",
                          )}
                        />
                      ) : (
                        <span
                          title={cell.title}
                          data-state={cell.state}
                          data-latest={latest || undefined}
                          className={cn(className, "block")}
                        >
                          <span className="sr-only">{cell.title}</span>
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-4 flex flex-wrap gap-4 text-[13px] text-ink-2" aria-hidden>
          <Key fill={FILL.pass}>Passed</Key>
          <Key fill={FILL.fail}>Failed</Key>
          {states.has("unjudged") ? <Key fill={FILL.unjudged}>Couldn't judge</Key> : null}
          <Key fill={FILL.none}>Not run</Key>
          <Key fill={cn("border", FILL.didnt_start)}>Run didn't start</Key>
          <span className="inline-flex items-center gap-1.5">
            <i className={cn("inline-block size-3 rounded-[3px] bg-sunk-2", NOW)} />
            Latest run
          </span>
        </div>
      </div>
    </section>
  );
}

function Key({ fill, children }: { fill: string; children: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i className={cn("inline-block size-3 rounded-[3px]", fill)} />
      {children}
    </span>
  );
}
