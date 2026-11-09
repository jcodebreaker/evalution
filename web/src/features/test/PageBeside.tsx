import { useState } from "react";
import type { Compare, PlanStep } from "../../lib/schema";
import { notTakenOf, notTakenWords, pageNextStep } from "../../lib/verdict";
import { Button } from "../../ui/button";
import { PictureDialog } from "../../ui/dialog";

const HEAD = "m-0 mb-1.5 text-[11.5px] font-semibold text-ink-3 uppercase";

/** The message a failed page try is read by: the first whose reply the page didn't show, else
 * the last that reached the page; with the message's count from 1. */
export function messageOf(steps: readonly PlanStep[]): { step: PlanStep; n: number } | null {
  const said = steps.filter((step) => step.kind === "say" && step.page);
  const at = said.findIndex((step) => step.page?.check?.outcome === "failed");
  const n = at >= 0 ? at : said.length - 1;
  const step = said[n];
  return step ? { step, n: n + 1 } : null;
}

/**
 * A page try that didn't pass, side by side (ADR 0010): what the page showed, its screenshot and
 * video, beside what the app answered, with how far the page showed it. `null` for a try that
 * passed, played through the API, or that the page map ended (its own note says that).
 */
export function PageBeside({ shown }: { shown: Compare }) {
  const [open, setOpen] = useState(false);
  const page = shown.page;
  const found = messageOf(shown.steps);
  if (!page || shown.outcome === "passed" || page.broken_map || !found) return null;
  const { step, n } = found;
  const seen = step.page;
  const reply = step.actual?.reply ?? "";
  const notTaken = notTakenOf(shown);
  const alt = `The page after message ${n}`;
  return (
    <section
      className="group-card grid grid-cols-2 max-md:grid-cols-1"
      aria-label="What the page showed, and what the app answered"
      data-testid="page-beside"
    >
      <div className="flex min-w-0 flex-col gap-2 p-3.5">
        <h4 className={HEAD}>What the page showed</h4>
        {seen?.shot_url ? (
          <button
            type="button"
            aria-label={`Enlarge the screenshot of the page after message ${n}`}
            onClick={() => setOpen(true)}
            className="self-start overflow-hidden rounded-lg ring-[0.5px] ring-separator hover:ring-tint focus-visible:outline-2 focus-visible:outline-ring"
          >
            <img
              src={seen.shot_url}
              alt={alt}
              loading="lazy"
              className="block max-h-[220px] w-full max-w-[360px] object-cover object-top"
            />
          </button>
        ) : (
          <p className="m-0 text-[13px] text-ink-2">No screenshot of the page was kept.</p>
        )}
        {seen?.shot_url ? (
          <span className="text-[12px] text-ink-3">{`After message ${n}`}</span>
        ) : null}
        {page.video?.url ? (
          // biome-ignore lint/a11y/useMediaCaption: a recording of a page, with no speech
          <video
            controls
            preload="metadata"
            src={page.video.url}
            aria-label="Recording of the try on the page"
            className="w-full max-w-[360px] rounded-lg ring-[0.5px] ring-separator"
          />
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-2 border-separator p-3.5 max-md:border-t md:border-s">
        <h4 className={HEAD}>What the app answered</h4>
        {reply ? (
          <p className="m-0 max-h-[260px] overflow-auto text-[13.5px] whitespace-pre-wrap">
            {reply}
          </p>
        ) : (
          <p className="m-0 text-[13.5px] text-ink-2">
            {notTaken ? `No reply: ${notTakenWords(notTaken).toLowerCase()}.` : "No reply"}
          </p>
        )}
        {seen?.part ? (
          <p className="m-0 text-[13px] text-ink-2" data-testid="page-part">
            {"Shown on the page: "}
            {seen.part.shown ? <code>{seen.part.shown}</code> : "none of it"}
            {" · Missing: "}
            <code>{seen.part.missing}</code>
          </p>
        ) : null}
      </div>
      {open && seen?.shot_url ? (
        <PictureDialog
          onClose={() => setOpen(false)}
          title={`After message ${n}, on the page`}
          src={seen.shot_url}
          alt={alt}
        />
      ) : null}
    </section>
  );
}

/** A page try that didn't pass ends in one sentence and the one button that follows it. */
export function PageNextStep({
  shown,
  appName,
  going,
  onRun,
}: {
  shown: Compare;
  appName: string;
  going: boolean;
  onRun: () => void;
}) {
  const next = pageNextStep(shown, appName);
  if (!next) return null;
  return (
    <section
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[14px] bg-bubble px-4 py-3.5"
      aria-label="Next step"
      data-testid="next-step"
    >
      <p className="m-0 min-w-0 flex-1 text-[14px]">
        <b>Next step:</b> {next.text}
      </p>
      <Button size="sm" disabled={going} onClick={onRun}>
        {next.button}
      </Button>
    </section>
  );
}
