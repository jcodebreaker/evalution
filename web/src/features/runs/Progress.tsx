import { cn } from "../../lib/cn";
import { progressText } from "../../lib/format";
import type { RunDetail } from "../../lib/schema";
import { LoadingState } from "../../ui/loading";
import { Group, GroupHeading } from "../shared/group";
import { VerdictGlyph } from "../shared/marks";

const GLYPH = { passed: "pass", failed: "fail", unjudged: "unjudged" } as const;

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
      <Group className="flex flex-col">
        <div className="flex flex-col gap-2.5 px-4 pt-4 pb-3.5">
          {run.analyzing ? (
            <LoadingState label="Looking at what went wrong…" />
          ) : (
            <>
              <span className="text-[28px] leading-[1.15] font-semibold tracking-[-0.5px] tabular-nums">
                {progressText(run)}
              </span>
              <span
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={total}
                aria-valuenow={done}
                aria-label={`${done} of ${total} tries`}
                className="h-1.5 overflow-hidden rounded-full bg-selection"
              >
                <span
                  className="block h-full rounded-full bg-foreground transition-[width] duration-500"
                  style={{ width: `${total ? (done / total) * 100 : 0}%` }}
                />
              </span>
              {current ? <LoadingState label={doing(current)} /> : null}
            </>
          )}
        </div>
      </Group>
      {finished.length ? (
        <section className="flex flex-col">
          <GroupHeading title="Finished" />
          <Group>
            {finished.map((entry) => (
              <button
                key={`${entry.row.case}:${entry.n}`}
                type="button"
                onClick={() => onTest(entry.row.case)}
                className="flex w-full items-center gap-2.5 border-separator px-3.5 py-2 text-start text-[14px] not-first:border-t hover:bg-selection focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
              >
                <VerdictGlyph verdict={GLYPH[entry.outcome]} />
                <span className="shrink-0 truncate max-md:max-w-[45%]">{entry.row.name}</span>
                <span className="shrink-0 text-[12.5px] text-ink-3">try {entry.n}</span>
                {entry.reason ? (
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate text-[13px]",
                      entry.outcome === "failed" ? "text-destructive" : "text-ink-2",
                    )}
                  >
                    {entry.label ? `${entry.label} · ${entry.reason}` : entry.reason}
                  </span>
                ) : null}
              </button>
            ))}
          </Group>
        </section>
      ) : null}
    </>
  );
}
