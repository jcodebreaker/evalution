import { Play } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "../../lib/cn";
import type { Compare } from "../../lib/schema";
import { PictureDialog } from "../../ui/dialog";
import { StatusDot } from "../../ui/status";
import { Card } from "./Card";
import { firstFrame, turnShown, turnsOf } from "./try-words";

/** A video's length as a clock reads it: `0:01`, `2:05`. */
export function clock(total: number): string {
  const whole = Math.max(0, Math.round(total));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

const FRAME =
  "flex w-[208px] shrink-0 flex-col overflow-hidden rounded-[10px] border border-separator bg-group p-0 text-start hover:border-rule-strong focus-visible:outline-2 focus-visible:outline-ring";
const PICKED = "border-tint ring-3 ring-tint-soft-2 hover:border-tint";
const CAPTION =
  "flex items-center justify-between gap-2 border-t border-separator px-3 py-2 text-[12px]";

/**
 * What the page showed over a try played through it: a filmstrip of the page after each message,
 * each with its mark, and the try's video as the last frame; the frame picked shows large below
 * (a screenshot opens larger still). `null` for a try through the API, or one that kept neither.
 */
export function PageFilm({ shown }: { shown: Compare }) {
  const page = shown.page;
  const frames = turnsOf(shown.steps).flatMap(({ step, n }) =>
    step.page ? [{ n, page: step.page }] : [],
  );
  const video = page?.video?.url ?? null;
  const start = firstFrame(shown.steps, !!video);
  const [picked, setPicked] = useState<number | "video" | null>(start);
  const [open, setOpen] = useState(false);
  const [length, setLength] = useState<number | null>(null);
  const key = `${shown.run}:${shown.try}`;
  // Another try shown: back to the frame it's read by.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset only when the try changes
  useEffect(() => {
    setPicked(start);
    setOpen(false);
    setLength(null);
  }, [key]);
  if (!page || (!video && !frames.some((frame) => frame.page.shot_url))) return null;
  const frame = typeof picked === "number" ? frames.find((entry) => entry.n === picked) : null;
  const alt = frame ? `The page after message ${frame.n}` : "";
  return (
    <Card title="What the page showed" testId="page-film">
      <div className="flex gap-3 overflow-x-auto px-5 pt-4 pb-4">
        {frames.map(({ n, page: seen }) => {
          const mark = turnShown(seen);
          return (
            <button
              key={n}
              type="button"
              aria-pressed={picked === n}
              aria-label={`Message ${n}: ${mark.text.toLowerCase()}`}
              onClick={() => setPicked(n)}
              className={cn(FRAME, picked === n && PICKED)}
            >
              {seen.shot_url ? (
                <img
                  src={seen.shot_url}
                  alt=""
                  loading="lazy"
                  className="block h-[128px] w-full bg-sunk object-cover object-top"
                />
              ) : (
                <span className="grid h-[128px] place-items-center bg-sunk px-3 text-center text-[12px] text-ink-3">
                  No screenshot kept
                </span>
              )}
              <span className={CAPTION}>
                <b className="font-medium">{`Message ${n}`}</b>
                <StatusDot state={mark.state} label={mark.text} />
              </span>
            </button>
          );
        })}
        {video ? (
          <button
            type="button"
            aria-pressed={picked === "video"}
            aria-label="Video of the try"
            onClick={() => setPicked("video")}
            className={cn(FRAME, picked === "video" && PICKED)}
          >
            <span className="relative grid h-[128px] place-items-center overflow-hidden bg-[#1f2133]">
              <video
                src={video}
                muted
                preload="metadata"
                aria-hidden
                tabIndex={-1}
                onLoadedMetadata={(event) => {
                  const said = event.currentTarget.duration;
                  if (Number.isFinite(said)) setLength(said);
                }}
                className="absolute inset-0 size-full object-cover object-top opacity-60"
              />
              <span className="relative grid size-10 place-items-center rounded-full bg-white/15 text-white">
                <Play className="size-4 fill-current" aria-hidden />
              </span>
            </span>
            <span className={CAPTION}>
              <b className="font-medium">Video</b>
              {length === null ? null : (
                <span className="font-mono text-[11.5px] text-ink-3 tabular-nums">
                  {clock(length)}
                </span>
              )}
            </span>
          </button>
        ) : null}
      </div>
      {frame?.page.shot_url ? (
        <div className="border-t border-separator bg-sunk p-4">
          <button
            type="button"
            aria-label={`Enlarge the screenshot of the page after message ${frame.n}`}
            onClick={() => setOpen(true)}
            className="block w-full overflow-hidden rounded-lg border border-separator bg-group hover:border-tint focus-visible:outline-2 focus-visible:outline-ring"
          >
            <img
              src={frame.page.shot_url}
              alt={alt}
              className="mx-auto block max-h-[520px] w-full object-contain object-top"
            />
          </button>
        </div>
      ) : frame ? (
        <p className="m-0 border-t border-separator px-5 py-4 text-[13px] text-ink-2">
          {`No screenshot of the page after message ${frame.n} was kept.`}
        </p>
      ) : null}
      {picked === "video" && video ? (
        <div className="border-t border-separator bg-sunk p-4">
          {/* biome-ignore lint/a11y/useMediaCaption: a recording of a page, with no speech */}
          <video
            controls
            preload="metadata"
            src={video}
            aria-label="Recording of the try on the page"
            className="mx-auto block max-h-[520px] w-full rounded-lg"
          />
        </div>
      ) : null}
      {open && frame?.page.shot_url ? (
        <PictureDialog
          onClose={() => setOpen(false)}
          title={`After message ${frame.n}, on the page`}
          src={frame.page.shot_url}
          alt={alt}
        />
      ) : null}
    </Card>
  );
}
