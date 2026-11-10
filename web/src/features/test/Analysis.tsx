import { ArrowRight, Sparkles } from "lucide-react";
import { seconds } from "../../lib/format";
import type { Divergence, Finding, Suspect } from "../../lib/schema";
import { ITS_READING, NOT_VERIFIED } from "../../lib/verdict";
import { Button } from "../../ui/button";
import { Chip, IconBox, Panel } from "../shared/kit";
import type { Entry } from "./one-list";

const SUSPECT: Record<Suspect, string> = { app: "App?", test: "Test?", unclear: "Unclear" };

/** What an evidence ref points at, in a few words: a call and when, else the ref itself. */
export function evidenceLabel(ref: string, entries: readonly Entry[]): string {
  if (!ref.startsWith("span:")) return ref.replace(/^(file|check|ref):/, "");
  const span = ref.slice("span:".length);
  const entry = entries.find((found) => found.span === span);
  if (!entry) return span;
  const name = (entry.made ?? entry.expected ?? entry.kind).split("(")[0];
  return entry.at === null ? (name ?? span) : `${name} · ${seconds(entry.at, true)}`;
}

/**
 * The try's analysis: the finding (summary, cause, fix, where the fault likely is) with its
 * evidence as chips that jump to the step, and where it first differs from a passing try.
 */
export function Analysis({
  finding,
  divergence,
  entries,
  passLabel,
  stepOf,
  onJump,
  onPassing,
}: {
  finding: Finding | null;
  /** Where this try first differs from a passing one, when it is the failing try. */
  divergence: Divergence | null;
  entries: readonly Entry[];
  /** The passing try in words: "try 1", "try 3 of Run 13". */
  passLabel: string;
  /** The step number (from 1) a span's call is at in "Step by step", else `null`. */
  stepOf: (span: string | null) => number | null;
  onJump: (span: string | null) => void;
  onPassing: () => void;
}) {
  const lead = finding?.summary ?? null;
  const detail = finding?.cause ?? null;
  if (!lead && !detail && !divergence) return null;
  const said = [lead, detail].flatMap((claim) => (claim ? [claim] : []));
  const refs = [...new Set(said.flatMap((claim) => claim.refs))];
  const step = divergence ? stepOf(divergence.fail.span) : null;
  return (
    <Panel aria-label="Analysis">
      <div className="flex items-start gap-4 p-5">
        <IconBox tone="tint">
          <Sparkles />
        </IconBox>
        <div className="flex min-w-0 flex-1 flex-col gap-2.5">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="m-0 text-[15px] font-semibold">{ITS_READING}</h2>
            {finding?.suspected ? <Chip tone="warn">{SUSPECT[finding.suspected]}</Chip> : null}
            {said.length && !said.every((claim) => claim.verified) ? (
              <Chip>{NOT_VERIFIED}</Chip>
            ) : null}
            <span className="flex-1" />
            {divergence ? (
              <Button
                variant="ghost"
                className="text-tint-ink hover:bg-tint-soft"
                onClick={() => onJump(divergence.fail.span)}
              >
                {`Differs from passing ${passLabel}${step ? ` at step ${step}` : ""}`}
                <ArrowRight />
              </Button>
            ) : null}
          </div>
          {lead || detail ? <p className="m-0">{(lead ?? detail)?.text}</p> : null}
          {lead && detail ? <p className="m-0 text-ink-2">{detail.text}</p> : null}
          {finding?.fix ? <p className="m-0 text-ink-2">{finding.fix}</p> : null}
          {divergence ? (
            <p className="m-0 text-sm text-ink-2">
              {divergence.detail}
              {" · "}
              <button
                type="button"
                className="text-tint-ink underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-tint"
                onClick={onPassing}
              >
                {`See the passing ${passLabel}`}
              </button>
            </p>
          ) : null}
          {refs.length ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="me-1 text-[13px] text-ink-3">Evidence</span>
              {refs.map((ref) =>
                ref.startsWith("span:") ? (
                  <button
                    key={ref}
                    type="button"
                    className="inline-flex h-[22px] items-center rounded-md bg-sunk px-[7px] font-mono text-xs text-ink-2 hover:bg-tint-soft hover:text-tint-ink focus-visible:outline-2 focus-visible:outline-tint"
                    onClick={() => onJump(ref.slice("span:".length))}
                  >
                    {evidenceLabel(ref, entries)}
                  </button>
                ) : (
                  <span
                    key={ref}
                    className="inline-flex h-[22px] items-center rounded-md bg-sunk px-[7px] font-mono text-xs text-ink-2"
                  >
                    {evidenceLabel(ref, entries)}
                  </span>
                ),
              )}
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}
