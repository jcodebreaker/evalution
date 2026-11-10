import { cn } from "../../lib/cn";
import { progressText } from "../../lib/format";
import type { RunDetail } from "../../lib/schema";
import { LoadingState } from "../../ui/loading";
import { StatusDot } from "../../ui/status";
import { Card, CardHead } from "./parts";

const DOT = { passed: "pass", failed: "fail", unjudged: "unjudged" } as const;
const SAID = { passed: "Passed", failed: "Failed", unjudged: "Couldn't judge" } as const;

/** What the going try is doing: its message while playing, else its stage. */
function doing(current: NonNullable<RunDetail["progress"]["current"]>): string {
  const stage =
    current.stage === "playing" && current.turns
      ? `message ${current.turn} of ${current.turns}`
      : current.stage;
  return [current.name, `try ${current.try}`, stage].filter(Boolean).join(" · ");
}

/** The going run: how far it is, what it plays now, and each try as it finishes. */
export function Progress({ run, onTest }: { run: RunDetail; onTest: (id: string) => void }) {
  const finished = run.cases
    .flatMap((row) =>
      row.tries.map((outcome, index) => ({
        row,
        n: index + 1,
        outcome,
        reason: row.reasons[index] ?? null,
        label: row.labels[index] ?? null,
      })),
    )
    .reverse();
  const done = Math.max(run.progress.done, finished.length);
  const total = Math.max(run.progress.total, done);
  const current = run.progress.current;
  return (
    <>
      <Card className="flex flex-col">
        <div className="flex flex-col gap-3 p-5">
          {run.analyzing ? (
            <LoadingState label="Looking at what went wrong…" />
          ) : (
            <>
              <span className="text-[30px] leading-none font-semibold tracking-[-0.03em] tabular-nums">
                {progressText(run)}
              </span>
              <span
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={total}
                aria-valuenow={done}
                aria-label={`${done} of ${total} tries`}
                className="h-2.5 overflow-hidden rounded-[5px] bg-sunk-2"
              >
                <span
                  className="block h-full bg-tint transition-[width] duration-500"
                  style={{ width: `${total ? (done / total) * 100 : 0}%` }}
                />
              </span>
              {current ? <LoadingState label={doing(current)} /> : null}
            </>
          )}
        </div>
      </Card>
      {finished.length ? (
        <Card className="flex flex-col">
          <CardHead title="Finished" />
          <div className="mt-3">
            {finished.map((entry) => (
              <button
                key={`${entry.row.case}:${entry.n}`}
                type="button"
                onClick={() => onTest(entry.row.case)}
                className="flex w-full items-center gap-3 border-t border-separator px-5 py-2.5 text-start text-sm hover:bg-sunk focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint"
              >
                <StatusDot state={DOT[entry.outcome]} label={SAID[entry.outcome]} />
                <span className="shrink-0 truncate max-md:max-w-[45%]">{entry.row.name}</span>
                <span className="shrink-0 text-sm text-ink-3">try {entry.n}</span>
                {entry.reason ? (
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate text-[13px]",
                      entry.outcome === "failed" ? "text-fail" : "text-ink-2",
                    )}
                  >
                    {entry.label ? `${entry.label} · ${entry.reason}` : entry.reason}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </Card>
      ) : null}
    </>
  );
}
