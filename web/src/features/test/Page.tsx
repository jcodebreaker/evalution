import { useState } from "react";
import { cn } from "../../lib/cn";
import { count, seconds } from "../../lib/format";
import type { StepPage, TryPage } from "../../lib/schema";
import { CHECKED_ON_PAGE, shownWords } from "../../lib/verdict";
import { PictureDialog } from "../../ui/dialog";
import { Fold } from "../shared/group";

/** What the app's page showed of one message: in a line, decided by code, with its screenshot. */
export function PageShown({ page, n }: { page: StepPage; n: number }) {
  const [open, setOpen] = useState(false);
  const said = shownWords(page);
  const alt = `The page after message ${n}`;
  return (
    <div className="mt-1 flex items-start gap-3" data-testid="page-shown">
      {page.shot_url ? (
        <button
          type="button"
          aria-label={`Enlarge the screenshot of the page after message ${n}`}
          onClick={() => setOpen(true)}
          className="shrink-0 overflow-hidden rounded-lg ring-[0.5px] ring-separator hover:ring-tint focus-visible:outline-2 focus-visible:outline-ring"
        >
          <img
            src={page.shot_url}
            alt={alt}
            loading="lazy"
            className="block h-[72px] w-[116px] object-cover object-top"
          />
        </button>
      ) : null}
      <div className="flex min-w-0 flex-col gap-0.5 text-[12.5px]">
        <span
          className={cn(
            "font-semibold break-words",
            said.held === true && "text-success",
            said.held === false && "text-destructive",
            said.held === null && "text-ink-2",
          )}
        >
          {said.text}
          {said.held !== false && page.shown_at !== null ? (
            <span className="font-normal text-ink-3">{` · at ${seconds(page.shown_at, true)}`}</span>
          ) : null}
        </span>
        <span className="text-[11.5px] text-ink-3">{CHECKED_ON_PAGE}, by code</span>
      </div>
      {open && page.shot_url ? (
        <PictureDialog
          onClose={() => setOpen(false)}
          title={`After message ${n}, on the page`}
          src={page.shot_url}
          alt={alt}
        />
      ) : null}
    </div>
  );
}

/** One line of what went wrong on the page: an error first, a warning after. */
export type PageLine = { tone: "error" | "warning"; text: string; at: number };

/** The page's console and failed requests, errors first, each in the order they came. */
export function pageLines(page: TryPage): PageLine[] {
  const requests = page.failed_requests.map(
    (request): PageLine => ({
      tone: "error",
      text: `${request.method} ${request.path}: ${request.status ?? "no answer"}`,
      at: request.at,
    }),
  );
  const console = page.console.map(
    (line): PageLine => ({
      tone: line.level === "error" ? "error" : "warning",
      text: line.text,
      at: line.at,
    }),
  );
  const lines = [...console, ...requests].sort((a, b) => a.at - b.at);
  return [
    ...lines.filter((line) => line.tone === "error"),
    ...lines.filter((line) => line.tone === "warning"),
  ];
}

/** Under the steps of a page try: its console and failed requests, its video, and quiet notes. */
export function PageNotes({ page }: { page: TryPage }) {
  const lines = pageLines(page);
  const errors = lines.filter((line) => line.tone === "error").length;
  const notes = [
    page.interrupted ? "The try was cut short before the page was closed." : null,
    page.video_missing ? `No video: ${page.video_missing}` : null,
    page.video && !page.video.url && !page.video_missing
      ? "Its video stayed on the computer that played it."
      : null,
  ].filter(Boolean);
  if (!lines.length && !page.video?.url && !notes.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {lines.length ? (
        <Fold
          title={`Console and failed requests · ${errors ? count(errors, "error") : count(lines.length, "warning")}`}
        >
          <ul className="group-card m-0 flex list-none flex-col p-0">
            {lines.map((line, index) => (
              <li
                key={index}
                className="grid grid-cols-[64px_minmax(0,1fr)_auto] items-baseline gap-2 border-separator px-3.5 py-2 text-[12.5px] not-first:border-t"
              >
                <span
                  className={cn(
                    "font-semibold",
                    line.tone === "error" ? "text-destructive" : "text-warning",
                  )}
                >
                  {line.tone === "error" ? "Error" : "Warning"}
                </span>
                <code className="font-mono text-[12px] break-words whitespace-pre-wrap">
                  {line.text}
                </code>
                <span className="text-[11.5px] text-ink-3 tabular-nums">
                  {seconds(line.at, true)}
                </span>
              </li>
            ))}
          </ul>
          {page.left_out ? (
            <p className="m-0 mt-1.5 text-[12px] text-ink-3">
              {count(page.left_out, "more line")} not kept.
            </p>
          ) : null}
        </Fold>
      ) : null}
      {page.video?.url ? (
        <Fold title="Video of the try">
          {/* biome-ignore lint/a11y/useMediaCaption: a recording of a page, with no speech */}
          <video
            controls
            preload="metadata"
            src={page.video.url}
            aria-label="Recording of the try on the page"
            className="w-full max-w-[720px] rounded-lg ring-[0.5px] ring-separator"
          />
        </Fold>
      ) : null}
      {notes.map((note) => (
        <p key={note} className="m-0 text-[12.5px] text-ink-2">
          {note}
        </p>
      ))}
    </div>
  );
}
