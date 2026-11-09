import { ago, count } from "../../lib/format";
import type { LearnedArea, LearnState, Understanding } from "../../lib/schema";
import { Button } from "../../ui/button";
import { type Outcome, StatusDot } from "../../ui/status";
import { Group, GroupHeading } from "../shared/group";
import { LearnAreaButton, type LearnAreaFn } from "./LearnArea";

/** Each state's mark and the words beside it. `learning` has its own mark: a pulsing dot. */
const LOOK: Record<LearnedArea["state"], { dot: Outcome | "learning"; said: string }> = {
  done: { dot: "pass", said: "Learned" },
  current: { dot: "pass", said: "Learned" },
  learning: { dot: "learning", said: "Learning" },
  due: { dot: "none", said: "Waiting" },
  failed: { dot: "fail", said: "Failed" },
  listed: { dot: "none", said: "Not learned" },
};

const learned = (area: LearnedArea) => area.state === "done" || area.state === "current";

/** An area's weight in plain words. The server sends a number only. */
export const weightText = (area: LearnedArea) =>
  area.state === "listed" || !area.weight ? "nothing in it the app uses" : `weight ${area.weight}`;

/** "6 of 9 areas learned · 2 learning · 1 not learned: nothing in it the app uses". */
export function areasSummary(areas: readonly LearnedArea[]): string {
  const of = (state: LearnedArea["state"]) => areas.filter((area) => area.state === state).length;
  const parts = [`${areas.filter(learned).length} of ${count(areas.length, "area")} learned`];
  if (of("learning")) parts.push(`${of("learning")} learning`);
  if (of("due")) parts.push(`${of("due")} waiting`);
  if (of("failed")) parts.push(`${of("failed")} failed`);
  if (of("listed")) parts.push(`${of("listed")} not learned: nothing in it the app uses`);
  return parts.join(" · ");
}

/** Where the learn stands, when the server says: `null` from an older server, or one that kept
 * no learn's progress (the page is then as it was). */
export function learnState(understanding: Understanding): LearnState | null {
  const found = understanding.progress;
  return found && (found.state || found.areas.length) ? found : null;
}

/** The overview is a first pass, written from the skeleton before the areas are in. */
export const isFirstPass = (understanding: Understanding) =>
  understanding.progress?.overview === "first_pass" ||
  understanding.overview?.state === "first_pass";

/** "First pass" and what it rests on, while the overview is written from the skeleton alone. */
export function FirstPass({ basis }: { basis: string | null }) {
  return (
    <p className="mx-[18px] mt-3 mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-ink-2">
      <span className="rounded-full bg-sunk-2 px-2 py-0.5 text-[11.5px] font-medium text-ink-2">
        First pass
      </span>
      <span>{basis ? `From ${basis}; it's` : "It's"} rewritten once the areas are learned.</span>
    </p>
  );
}

function Mark({ area }: { area: LearnedArea }) {
  const look = LOOK[area.state];
  if (look.dot === "learning") {
    return (
      <span
        role="img"
        aria-label={look.said}
        title={look.said}
        className="inline-block size-2 shrink-0 animate-pulse rounded-full bg-tint align-middle"
      />
    );
  }
  return <StatusDot state={look.dot} label={look.said} />;
}

function AreaRow({ area, onLearnArea }: { area: LearnedArea; onLearnArea?: LearnAreaFn }) {
  const look = LOOK[area.state];
  const quiet = area.state === "due" || area.state === "listed";
  const said =
    area.state === "current" && area.learned_at ? `Learned ${ago(area.learned_at)}` : look.said;
  return (
    <li
      aria-label={area.title || area.id}
      data-state={area.state}
      className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 border-separator px-4 py-2 not-last:border-b"
    >
      <Mark area={area} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className={quiet ? "truncate text-[14px] text-ink-2" : "truncate text-[14px]"}>
          {area.title || area.id}
        </span>
        <span className="text-[12px] text-ink-3">
          {count(area.files, "file")} · {weightText(area)}
        </span>
      </span>
      {area.state === "listed" && onLearnArea ? (
        <LearnAreaButton area={area.id} onLearn={onLearnArea} />
      ) : (
        <span className="text-[12.5px] text-ink-2">{said}</span>
      )}
    </li>
  );
}

/** "Stopped after 6 of 9 areas; what was learned is kept.", and "Go on" to learn the rest. */
export function Stopped({
  progress,
  busy,
  onGoOn,
}: {
  progress: LearnState;
  busy: boolean;
  onGoOn: () => void;
}) {
  const done = progress.areas.filter(learned).length;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-[10px] bg-sunk px-3.5 py-2.5 text-[13px] text-ink-2"
    >
      <span className="min-w-0 flex-1">
        Stopped after {done} of {count(progress.areas.length, "area")}; what was learned is kept.
      </span>
      <Button variant="secondary" size="sm" disabled={busy} onClick={onGoOn}>
        Go on
      </Button>
    </div>
  );
}

/** The app's areas, heaviest first: what each is, how big, and whether it is learned. A listed
 * area (nothing in it the app uses) offers "Learn this area" (*onLearnArea*). */
export function LearnedAreas({
  progress,
  busy,
  onGoOn,
  onLearnArea,
}: {
  progress: LearnState;
  /** A learn is running: "Go on" waits. */
  busy: boolean;
  onGoOn: () => void;
  onLearnArea?: LearnAreaFn;
}) {
  if (!progress.areas.length && progress.state !== "stopped") return null;
  return (
    <section aria-label="Areas" className="flex flex-col gap-2">
      {progress.state === "stopped" ? (
        <Stopped progress={progress} busy={busy} onGoOn={onGoOn} />
      ) : null}
      {progress.areas.length ? (
        <div>
          <GroupHeading title="Areas" />
          <Group className="overflow-hidden">
            <p className="m-0 border-separator border-b px-4 py-2.5 text-[12.5px] text-ink-2">
              {areasSummary(progress.areas)}
            </p>
            <ul className="m-0 list-none p-0">
              {progress.areas.map((area) => (
                <AreaRow key={area.id} area={area} onLearnArea={onLearnArea} />
              ))}
            </ul>
          </Group>
        </div>
      ) : null}
    </section>
  );
}
