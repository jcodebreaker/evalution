import { cn } from "../../lib/cn";
import { asSentence, seconds } from "../../lib/format";
import type { Compare, TryOutcome } from "../../lib/schema";
import {
  layerWords,
  NEVER_REACHED,
  notTakenOf,
  PAGE_ONLY,
  PASSED_MODEL_ONLY,
  retriedWords,
  tryWord,
} from "../../lib/verdict";
import { StatusDot } from "../../ui/status";
import { breakCells, markOf } from "./try-words";

const MODEL_ONLY = PASSED_MODEL_ONLY.split(", ")[1];

/**
 * The try shown, failed or passed, in a line, with the way to the other tries; under it, where it
 * broke: each layer of the try (Stored, Answered, Shown, Page healthy) with its mark and one plain
 * line, the one that broke on a soft red wash.
 */
export function VerdictBanner({
  shown,
  runLabel,
  onTry,
}: {
  shown: Compare;
  runLabel: (run: string) => string;
  onTry: (n: number) => void;
}) {
  const others = shown.tries.filter((entry) => entry.n !== shown.try);
  const those = (outcome: TryOutcome) =>
    others.filter((entry) => entry.outcome === outcome).map((entry) => entry.n);
  const tries = (ns: number[]) =>
    `${ns.length === 1 ? "Try" : "Tries"} ${ns.slice(0, -1).join(", ")}${ns.length > 1 ? " and " : ""}${ns[ns.length - 1]}`;
  const said = (ns: number[], words: string) => (ns.length ? `${tries(ns)} ${words}.` : null);
  const rest = [
    said(those("passed"), "passed"),
    said(those("failed"), "failed"),
    said(those("unjudged"), "couldn't be judged"),
  ];
  const lead = shown.tries.length === 1 ? "" : ` on try ${shown.try}`;
  // Played through the page: plain words when only the page failed, or the app didn't take it.
  const pageOnly = shown.page?.page_only ?? false;
  const layered = layerWords(
    shown.layers,
    shown.outcome,
    shown.page?.without_page,
    pageOnly,
    notTakenOf(shown),
    shown.page
      ? { healthy: shown.page.health?.outcome ?? null, shownAlone: shown.page.shown_alone }
      : null,
  );
  const cells = breakCells(shown);
  return (
    <section
      className="min-w-0 overflow-hidden rounded-2xl border border-rule bg-group shadow-[0_1px_2px_var(--shadow)]"
      aria-label="Where it broke"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5 p-5">
        <div className="flex min-w-0 flex-1 items-baseline gap-2.5">
          <StatusDot
            state={markOf(shown.outcome)}
            label={tryWord(shown.outcome)}
            className="size-2.5"
          />
          <div className="flex min-w-0 flex-col gap-0.5">
            <b className="text-[22px] leading-tight font-semibold tracking-[-0.02em]">
              {layered?.headline
                ? `${layered.headline}${lead ? ` · try ${shown.try}` : ""}`
                : `${shown.label ?? tryWord(shown.outcome)}${lead}${shown.passed && shown.different_route ? ", by a different route" : ""}${shown.passed && shown.basis === "model_only" ? `, ${MODEL_ONLY}` : ""}${shown.passed && pageOnly ? `, ${PAGE_ONLY}` : ""}`}
            </b>
            <small className="mt-1 text-sm text-ink-2">
              {[
                layered?.line ?? null,
                shown.passed || !shown.reason ? null : asSentence(shown.reason),
                shown.called ? null : NEVER_REACHED,
                ...retriedWords(shown.retried),
                ...rest,
                runLabel(shown.run),
              ]
                .filter(Boolean)
                .join(" ")}
              {shown.seconds === null ? null : ` · ${seconds(shown.seconds)}`}
            </small>
          </div>
        </div>
        {shown.tries.length > 1 ? (
          <fieldset
            className="m-0 inline-flex flex-wrap gap-0.5 rounded-[11px] border-0 bg-sunk p-[3px]"
            aria-label="Tries"
          >
            {shown.tries.map((entry) => (
              <button
                key={entry.n}
                type="button"
                aria-pressed={entry.n === shown.try}
                aria-label={`Try ${entry.n}, ${tryWord(entry.outcome).toLowerCase()}`}
                onClick={() => onTry(entry.n)}
                className={cn(
                  "flex h-8 items-center gap-[7px] rounded-lg px-3 text-sm font-medium text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-tint",
                  entry.n === shown.try && "bg-group text-ink shadow-[0_1px_2px_var(--shadow-2)]",
                )}
              >
                <StatusDot state={markOf(entry.outcome)} label={tryWord(entry.outcome)} />
                Try {entry.n}
              </button>
            ))}
          </fieldset>
        ) : null}
      </div>
      {cells ? (
        <ul
          aria-label="Which layer held"
          className="m-0 flex list-none border-t border-rule p-0 max-md:flex-col"
          data-testid="where-it-broke"
        >
          {cells.map((cell) => (
            <li
              key={cell.layer}
              title={cell.label}
              data-broke={cell.state === "fail" || undefined}
              className={cn(
                "flex min-w-0 flex-1 items-baseline gap-2.5 px-5 py-3.5 not-first:border-rule md:not-first:border-s max-md:not-first:border-t",
                cell.state === "fail" && "bg-fail-soft",
              )}
            >
              <StatusDot state={cell.state} label={cell.label} className="translate-y-[-1px]" />
              <span className="flex min-w-0 flex-col">
                <b
                  className={cn(
                    "font-semibold",
                    cell.state === "fail" && "text-fail",
                    cell.state === "none" && "text-ink-3",
                  )}
                >
                  {cell.name}
                </b>
                <small className="mt-0.5 line-clamp-2 text-[13px] break-words text-ink-2">
                  {cell.line}
                </small>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
