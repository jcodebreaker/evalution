import { useState } from "react";
import { cn } from "../../lib/cn";
import { seconds } from "../../lib/format";
import type { CaseRow, Layers } from "../../lib/schema";
import {
  layerLine,
  originallyWord,
  PASSED_MODEL_ONLY,
  unjudgedWords,
  verdictWord,
} from "../../lib/verdict";
import { type Outcome, StatusDot } from "../../ui/status";
import { PlayButton } from "../shared/kit";
import { PlaysChip } from "../shared/marks";

/** A row's line under its name: why a failing test failed, else what it checks. A test played
 * through the page says which layer held first (`Stored ✓ · Answered ✓ · Shown ✕`), then why, in
 * the reason's own plain words, without the label its ✕ already says. */
export function rowLine(row: CaseRow): string | null {
  const layered = layerLine(row.layers);
  if (layered) return [layered, reasonLine(row)].filter(Boolean).join(" · ");
  return reasonLine(row);
}

/** A row's reason alone, for the results table, whose columns say the layers: why a failing test
 * failed, else what it checks. */
export function reasonLine(row: CaseRow): string | null {
  const failing = row.verdict === "fail" || row.verdict === "flaky";
  const reason = row.reasons.find((found) => found) ?? null;
  if (layerLine(row.layers)) {
    return row.verdict === "unjudged"
      ? unjudgedWords(row.unjudged)
      : failing
        ? reason
        : row.verdict === "pass" && row.basis === "model_only"
          ? PASSED_MODEL_ONLY
          : row.note;
  }
  return row.verdict === "unjudged"
    ? `${row.label ?? verdictWord("unjudged")} · ${unjudgedWords(row.unjudged)}`
    : failing
      ? [row.label, reason ?? row.about].filter(Boolean).join(" · ")
      : row.note
        ? row.note
        : row.verdict === "pass" && row.basis === "model_only"
          ? PASSED_MODEL_ONLY
          : row.about;
}

const capital = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

/** A test's verdict as the one dot: flaky reads as a failure, said "Flaky". */
function rowDot(row: CaseRow): { state: Outcome; label: string } {
  if (!row.verdict) return { state: "none", label: "Not run" };
  const state: Outcome =
    row.verdict === "pass" ? "pass" : row.verdict === "unjudged" ? "unjudged" : "fail";
  return { state, label: verdictWord(row.verdict) };
}

const LAYERS: { layer: keyof Layers; label: string }[] = [
  { layer: "stored", label: "Stored" },
  { layer: "answered", label: "Answered" },
  { layer: "shown", label: "Shown" },
  { layer: "page_health", label: "Page healthy" },
];

const HELD: Record<string, { state: Outcome; label: string }> = {
  pass: { state: "pass", label: "Held" },
  fail: { state: "fail", label: "Failed" },
  unjudged: { state: "unjudged", label: "Couldn't judge" },
};

/** One layer of a row: a dot for how it held, "–" where it says nothing. */
function LayerCell({ row, layer, label }: { row: CaseRow; layer: keyof Layers; label: string }) {
  const held = row.layers ? HELD[row.layers[layer] ?? "absent"] : undefined;
  return (
    <td className="px-3 py-3 text-center">
      {held ? (
        <StatusDot state={held.state} label={`${label}: ${held.label}`} />
      ) : (
        <span className="text-ink-3" title={`${label}: not checked`}>
          –
        </span>
      )}
    </td>
  );
}

type Tab = "all" | "failed" | "not_run";

const failingRow = (row: CaseRow) => row.verdict === "fail" || row.verdict === "flaky";

/** The results: All, Failed and Not run, each a table of the tests with a dot, the name and why
 * in a line; where any test played through the page, how each layer held (Stored, Answered,
 * Shown, Page healthy) as dot columns; where tests played both ways, Page or API by each played
 * test's name; then its tries
 * and time. `why`: why a test that didn't play didn't, as the run says it. */
export function Results({
  rows,
  going,
  onTest,
  onRun,
  why,
}: {
  rows: CaseRow[];
  going: boolean;
  onTest: (id: string) => void;
  onRun: (row: CaseRow) => void;
  why?: (id: string) => string | null;
}) {
  const [tab, setTab] = useState<Tab>("all");
  // Page or API by each played test, where the run played both ways: one way needs no badge.
  const played = rows.filter((row) => row.tries.length);
  const paged =
    played.some((row) => row.plays === "page") && played.some((row) => row.plays === "api");
  const layered = rows.some((row) => layerLine(row.layers));
  // The page's health is a column only where a row says it (an older server's rows don't).
  const columns = LAYERS.filter(
    (entry) => entry.layer !== "page_health" || rows.some((row) => row.layers?.page_health),
  );
  const failed = rows.filter(failingRow);
  const notRun = rows.filter((row) => !row.verdict);
  const tabs: { id: Tab; label: string; n: number }[] = [
    { id: "all", label: "All", n: rows.length },
    { id: "failed", label: "Failed", n: failed.length },
    { id: "not_run", label: "Not run", n: notRun.length },
  ];
  const shownTabs = tabs.filter((entry) => entry.id === "all" || entry.n > 0);
  // A tab emptied by a reload shows them all.
  const open = shownTabs.some((entry) => entry.id === tab) ? tab : "all";
  const shown = open === "failed" ? failed : open === "not_run" ? notRun : rows;
  return (
    <div className="flex min-w-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-rule px-5">
        {shownTabs.length > 1 ? (
          <div role="tablist" aria-label="Results" className="flex gap-[22px]">
            {shownTabs.map((entry) => (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={open === entry.id}
                onClick={() => setTab(entry.id)}
                className={cn(
                  "-mb-px inline-flex h-11 items-center gap-1.5 border-b-2 px-1 font-medium focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint",
                  open === entry.id
                    ? "border-tint text-ink"
                    : "border-transparent text-ink-2 hover:text-foreground",
                )}
              >
                {entry.label}
                <span className={cn("font-mono text-xs font-medium text-ink-3")}>{entry.n}</span>
              </button>
            ))}
          </div>
        ) : (
          <h3 className="m-0 py-2.5 text-[16px] font-semibold">Results</h3>
        )}
        {layered ? (
          <p
            className="m-0 ms-auto flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-[13px] text-ink-2"
            data-testid="layer-key"
          >
            {Object.values(HELD).map((held) => (
              <span key={held.label} className="inline-flex items-center gap-1.5">
                <StatusDot state={held.state} label={held.label} />
                {held.label}
              </span>
            ))}
          </p>
        ) : null}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="text-[13px] text-ink-3">
              <th className="border-b border-rule px-5 py-2.5 text-start font-medium">Test</th>
              {layered
                ? columns.map((entry) => (
                    <th
                      key={entry.layer}
                      className="border-b border-rule px-3 py-2.5 text-center font-medium whitespace-nowrap"
                    >
                      {entry.label}
                    </th>
                  ))
                : null}
              <th className="border-b border-rule px-3 py-2.5 text-end font-medium">Tries</th>
              <th className="border-b border-rule py-2.5 ps-3 pe-5 text-end font-medium">Time</th>
              <th className="w-0 pe-5">
                <span className="sr-only">Run</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const failing = failingRow(row);
              const line = row.verdict ? reasonLine(row) : (why?.(row.case) ?? reasonLine(row));
              const said = line ? capital(line) : null;
              const shownLine = row.originally
                ? [originallyWord(row.originally), said].filter(Boolean).join(" · ")
                : said;
              const dot = rowDot(row);
              const passedTries = row.tries.filter((outcome) => outcome === "passed").length;
              return (
                <tr
                  key={row.case}
                  onClick={() => onTest(row.case)}
                  className="group/row border-t border-rule first:border-t-0 hover:bg-sunk"
                >
                  <td className="max-w-0 min-w-[240px] px-5 py-3.5">
                    <div className="flex min-w-0 items-center gap-3">
                      <StatusDot state={dot.state} label={dot.label} className="size-2.5" />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="flex min-w-0 items-center gap-2">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              onTest(row.case);
                            }}
                            className={cn(
                              "min-w-0 truncate text-start font-medium focus-visible:outline-2 focus-visible:outline-tint",
                              !row.verdict && "text-ink-2",
                            )}
                          >
                            {row.name}
                          </button>
                          {paged && row.tries.length ? <PlaysChip plays={row.plays} /> : null}
                        </span>
                        {shownLine ? (
                          <span
                            title={shownLine}
                            className={cn(
                              "truncate text-[13px]",
                              failing ? "text-ink-2" : "text-ink-3",
                            )}
                          >
                            {shownLine}
                          </span>
                        ) : null}
                      </span>
                    </div>
                  </td>
                  {layered
                    ? columns.map((entry) => <LayerCell key={entry.layer} row={row} {...entry} />)
                    : null}
                  <td className="px-3 py-3 text-end font-mono text-[13px] whitespace-nowrap text-ink-2 tabular-nums">
                    {row.tries.length ? (
                      <span title={`${passedTries} of ${row.tries.length} tries passed`}>
                        {`${passedTries}/${row.tries.length}`}
                      </span>
                    ) : (
                      <span className="text-ink-3">–</span>
                    )}
                  </td>
                  <td className="py-3 ps-3 pe-5 text-end font-mono text-[13px] whitespace-nowrap tabular-nums">
                    {row.seconds === null ? (
                      <span className="text-ink-3">–</span>
                    ) : (
                      seconds(row.seconds)
                    )}
                  </td>
                  <td className="py-2 pe-5">
                    <PlayButton
                      label={`Run ${row.name}`}
                      disabled={going}
                      onClick={() => onRun(row)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
