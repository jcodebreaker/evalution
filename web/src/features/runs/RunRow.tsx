import { live, progressText, spent, when } from "../../lib/format";
import type { RunSummary, RunTest } from "../../lib/schema";
import { originallyWord, unjudgedWords, verdictLine } from "../../lib/verdict";
import { Spinner } from "../../ui/loading";
import { VerdictGlyph } from "../shared/marks";

export type Entry = { run: RunSummary; test: RunTest };

export function RunRow({
  entry,
  label,
  onOpen,
}: {
  entry: Entry;
  label: string;
  onOpen: () => void;
}) {
  const { run, test } = entry;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full flex-wrap items-center gap-x-4 gap-y-0.5 border-separator px-3.5 py-2.5 text-start not-first:border-t hover:bg-selection focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
    >
      <span className="flex min-w-0 flex-1 basis-[200px] items-center gap-2.5">
        <Outcome entry={entry} />
        <span className="truncate text-ink-3">{label}</span>
      </span>
      <span className="flex items-center gap-4 text-[12px] text-ink-2 tabular-nums">
        {test.tries ? <span>{`${test.passed} of ${test.tries} tries`}</span> : null}
        {spent(test.usd) ? <span>{spent(test.usd)}</span> : null}
        <span className="text-ink-3">{when(run.started_at)}</span>
      </span>
    </button>
  );
}

/** What this test's run came to: the verdict, or why there is none. */
function Outcome({ entry }: { entry: Entry }) {
  const { run, test } = entry;
  if (live(run)) {
    return (
      <span className="inline-flex items-center gap-1.5 text-foreground">
        <Spinner />
        {run.analyzing ? "Analyzing" : progressText(run)}
      </span>
    );
  }
  const why = test.reason ? ` · ${test.reason}` : "";
  const was = test.originally ? ` · ${originallyWord(test.originally)}` : "";
  if (test.verdict === "unjudged")
    return (
      <span
        title={`${unjudgedWords(test.unjudged)}${was}`}
        className="inline-flex items-center gap-1.5 text-ink-2"
      >
        <VerdictGlyph verdict={test.verdict} />
        {verdictLine(test)}
      </span>
    );
  if (test.verdict) {
    return (
      <span className="inline-flex items-center gap-1.5 text-foreground">
        <VerdictGlyph verdict={test.verdict} />
        {`${verdictLine(test)}${test.note ? ` · ${test.note}` : ""}${was}`}
      </span>
    );
  }
  if (run.interrupted) return <span className="text-ink-2">Interrupted</span>;
  if (run.status === "stopped") return <span className="text-ink-2">Stopped</span>;
  return <span className="text-destructive">{`Didn't finish${why}`}</span>;
}
