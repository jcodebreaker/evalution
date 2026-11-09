import { cn } from "../../lib/cn";
import { count, seconds } from "../../lib/format";
import type { StepPage, TryPage } from "../../lib/schema";
import { CHECKED_ON_PAGE, shownWords } from "../../lib/verdict";
import { Fold } from "../shared/group";
import { MissingPieceNote } from "../shared/next-step";

/** What the app's page showed of one message, in a line, decided by code. */
export function PageShown({ page }: { page: StepPage }) {
  const said = shownWords(page);
  return (
    <p className="m-0 mt-1 flex flex-wrap gap-x-1.5 text-[13px]" data-testid="page-shown">
      <span className={cn("break-words", said.held === false ? "text-fail" : "text-ink-2")}>
        {said.text}
      </span>
      <span className="text-ink-3">· {CHECKED_ON_PAGE}, by code</span>
    </p>
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

/** Under the steps of a page try: its console and failed requests, its video (unless `video` is
 * false: shown already beside what the app answered), and quiet notes. */
export function PageNotes({ page, video = true }: { page: TryPage; video?: boolean }) {
  const lines = pageLines(page);
  const errors = lines.filter((line) => line.tone === "error").length;
  const notes = [
    page.interrupted ? "The try was cut short before the page was closed." : null,
    page.video_missing && !page.video_fix ? `No video: ${page.video_missing}` : null,
    page.shots_missing ? `No screenshots: ${page.shots_missing}` : null,
    page.video && !page.video.url && !page.video_missing
      ? "Its video stayed on the computer that played it."
      : null,
  ].filter(Boolean);
  const film = video ? page.video?.url : null;
  if (!lines.length && !film && !notes.length && !page.video_fix) return null;
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
                className="grid grid-cols-[64px_minmax(0,1fr)_auto] items-baseline gap-2 border-separator px-3.5 py-2 text-[13px] not-first:border-t"
              >
                <span
                  className={cn(
                    "font-semibold",
                    line.tone === "error" ? "text-fail" : "text-warning",
                  )}
                >
                  {line.tone === "error" ? "Error" : "Warning"}
                </span>
                <code className="font-mono text-[12.5px] break-words whitespace-pre-wrap">
                  {line.text}
                </code>
                <span className="text-[11.5px] text-ink-3 tabular-nums">
                  {seconds(line.at, true)}
                </span>
              </li>
            ))}
          </ul>
          {page.left_out ? (
            <p className="m-0 mt-1.5 text-[12.5px] text-ink-3">
              {count(page.left_out, "more line")} not kept.
            </p>
          ) : null}
        </Fold>
      ) : null}
      {film ? (
        <Fold title="Video of the try">
          {/* biome-ignore lint/a11y/useMediaCaption: a recording of a page, with no speech */}
          <video
            controls
            preload="metadata"
            src={film}
            aria-label="Recording of the try on the page"
            className="w-full max-w-[720px] rounded-lg ring-[0.5px] ring-separator"
          />
        </Fold>
      ) : null}
      {page.video_fix ? <MissingPieceNote piece={page.video_fix} /> : null}
      {notes.map((note) => (
        <p key={note} className="m-0 text-[13px] text-ink-2">
          {note}
        </p>
      ))}
    </div>
  );
}
