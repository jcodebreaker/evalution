import { when } from "../../lib/format";
import type { CaseRow } from "../../lib/schema";
import { originallyWord, PASSED_MODEL_ONLY, unjudgedWords } from "../../lib/verdict";
import { RunAction } from "../runs/RunAction";
import { NotRunGlyph, StaleChip, VerdictGlyph } from "../shared/marks";

export function TestRow({
  row,
  going,
  onOpen,
  onRun,
}: {
  row: CaseRow;
  going: boolean;
  onOpen: () => void;
  onRun: () => void;
}) {
  const line =
    row.verdict === "unjudged"
      ? unjudgedWords(row.unjudged)
      : row.note
        ? row.note
        : row.verdict === "pass" && row.basis === "model_only"
          ? PASSED_MODEL_ONLY
          : row.about;
  return (
    <div className="group/row flex items-center gap-2 border-separator pe-2.5 not-first:border-t hover:bg-selection">
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 items-start gap-2.5 py-2.5 ps-3.5 text-start focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      >
        {row.verdict ? (
          <VerdictGlyph verdict={row.verdict} className="mt-[3px]" />
        ) : (
          <NotRunGlyph className="mt-[3px]" />
        )}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate">{row.name}</span>
            {row.stale ? <StaleChip reason={row.stale} /> : null}
            {row.different ? (
              <span className="shrink-0 text-[12px] text-ink-3">Different route</span>
            ) : null}
            {row.originally ? (
              <span className="shrink-0 text-[12px] text-ink-3">
                {originallyWord(row.originally)}
              </span>
            ) : null}
          </span>
          {line ? (
            <span title={line} className="truncate text-[12px] text-ink-3">
              {line}
            </span>
          ) : null}
        </span>
        <span className="shrink-0 pt-px text-[12px] text-ink-3 tabular-nums">
          {row.last_at ? when(row.last_at) : row.verdict === null ? "Not run" : null}
        </span>
      </button>
      {going ? null : <RunAction name={row.name} onRun={onRun} />}
    </div>
  );
}
