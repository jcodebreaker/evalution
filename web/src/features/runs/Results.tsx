import { cn } from "../../lib/cn";
import type { CaseRow } from "../../lib/schema";
import { originallyWord, PASSED_MODEL_ONLY, unjudgedWords, verdictWord } from "../../lib/verdict";
import { Group } from "../shared/group";
import { NotRunGlyph, TryDots, VerdictGlyph } from "../shared/marks";
import { RunAction } from "./RunAction";

/** The results: verdict, name, tries, and why a failing test failed (else what it checks). */
export function Results({
  rows,
  going,
  onTest,
  onRun,
}: {
  rows: CaseRow[];
  going: boolean;
  onTest: (id: string) => void;
  onRun: (row: CaseRow) => void;
}) {
  return (
    <Group>
      {rows.map((row) => {
        const failing = row.verdict === "fail" || row.verdict === "flaky";
        const line =
          row.verdict === "unjudged"
            ? `${row.label ?? verdictWord("unjudged")} · ${unjudgedWords(row.unjudged)}`
            : failing
              ? [row.label, row.reasons.find((reason) => reason) ?? row.about]
                  .filter(Boolean)
                  .join(" · ")
              : row.note
                ? row.note
                : row.verdict === "pass" && row.basis === "model_only"
                  ? PASSED_MODEL_ONLY
                  : row.about;
        const shown = row.originally ? `${originallyWord(row.originally)} · ${line}` : line;
        return (
          <div
            key={row.case}
            className="group/row flex items-center gap-2 border-separator pe-2.5 not-first:border-t hover:bg-selection"
          >
            <button
              type="button"
              onClick={() => onTest(row.case)}
              className="flex min-w-0 flex-1 items-center gap-2.5 py-2.5 ps-3.5 text-start focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
            >
              {row.verdict ? <VerdictGlyph verdict={row.verdict} /> : <NotRunGlyph />}
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">{row.name}</span>
                {shown ? (
                  <span
                    className={cn("truncate text-[12px]", failing ? "text-ink-2" : "text-ink-3")}
                  >
                    {shown}
                  </span>
                ) : null}
              </span>
              <TryDots tries={row.tries} />
            </button>
            {going ? null : <RunAction name={row.name} onRun={() => onRun(row)} />}
          </div>
        );
      })}
    </Group>
  );
}
