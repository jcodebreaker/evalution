import { ago, count } from "../../lib/format";
import type { AreaView, LearnStateView, ProfileFit, UnderstandingView } from "../../lib/schema";
import { Button } from "../../ui/button";
import { type Outcome, StatusDot } from "../../ui/status";
import { Card, Chip } from "../connect/kit";
import { Fold } from "../shared/group";
import { LearnAreaButton, type LearnAreaFn } from "./LearnArea";

/** Each state's mark and the words beside it. `learning` has its own mark: a pulsing dot. */
const LOOK: Record<AreaView["state"], { dot: Outcome | "learning"; said: string }> = {
  done: { dot: "pass", said: "Learned" },
  current: { dot: "pass", said: "Learned" },
  learning: { dot: "learning", said: "Learning" },
  due: { dot: "none", said: "Waiting" },
  failed: { dot: "fail", said: "Failed" },
  listed: { dot: "none", said: "Not learned" },
  outside: { dot: "none", said: "Not part of the agent" },
};

const learned = (area: AreaView) => area.state === "done" || area.state === "current";

/** An area's weight in plain words. The server sends a number only. */
export const weightText = (area: AreaView) =>
  area.state === "outside"
    ? "not part of the agent"
    : area.state === "listed" || !area.weight
      ? "nothing in it the app uses"
      : `weight ${area.weight}`;

/** "6 of 9 areas learned · 2 learning · 1 not learned: nothing in it the app uses". */
export function areasSummary(areas: readonly AreaView[]): string {
  const of = (state: AreaView["state"]) => areas.filter((area) => area.state === state).length;
  const parts = [`${areas.filter(learned).length} of ${count(areas.length, "area")} learned`];
  if (of("learning")) parts.push(`${of("learning")} learning`);
  if (of("due")) parts.push(`${of("due")} waiting`);
  if (of("failed")) parts.push(`${of("failed")} failed`);
  if (of("listed")) parts.push(`${of("listed")} not learned: nothing in it the app uses`);
  if (of("outside")) parts.push(`${of("outside")} not part of the agent`);
  return parts.join(" · ");
}

/** Where the learn stands, when the server says: `null` from an older server, or one that kept
 * no learn's progress (the page is then as it was). */
export function learnState(understanding: UnderstandingView): LearnStateView | null {
  const found = understanding.progress;
  return found && (found.state || found.areas.length) ? found : null;
}

/** The overview is a first pass, written from the skeleton before the areas are in. */
export const isFirstPass = (understanding: UnderstandingView) =>
  understanding.progress?.overview === "first_pass" ||
  understanding.overview?.state === "first_pass";

/** Only the facts are learned (C1): the card and the diagram come from the code and real use,
 * and the areas are read when a test needs them. */
export const isFacts = (understanding: UnderstandingView) =>
  understanding.overview?.state === "facts" ||
  (!understanding.overview && understanding.progress?.overview === "facts");

/** "From the code and 12 conversations. Areas are read when a test needs them.", as a pill. */
export function FactsPill({ basis }: { basis: string | null }) {
  return (
    <p className="m-0 mb-3">
      <span className="inline-block rounded-full bg-sunk-2 px-2.5 py-0.5 text-[13px] text-ink-2">
        From {basis ?? "the code"}. Areas are read when a test needs them.
      </span>
    </p>
  );
}

/** "First pass" and what it rests on, while the overview is written from the skeleton alone. */
export function FirstPass({ basis }: { basis: string | null }) {
  return (
    <p className="m-0 mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-2">
      <span className="rounded-full bg-sunk-2 px-2 py-0.5 text-[13px] font-medium text-ink-2">
        First pass
      </span>
      <span>{basis ? `From ${basis}; it's` : "It's"} rewritten once the areas are learned.</span>
    </p>
  );
}

function Mark({ area, label }: { area: AreaView; label?: string }) {
  const look = LOOK[area.state];
  const said = label ?? look.said;
  if (look.dot === "learning") {
    return (
      <span
        role="img"
        aria-label={said}
        title={said}
        className="inline-block size-2.5 shrink-0 animate-pulse rounded-full bg-tint align-middle"
      />
    );
  }
  return <StatusDot state={look.dot} label={said} className="size-2.5" />;
}

function AreaRow({
  area,
  onLearnArea,
  waiting = false,
}: {
  area: AreaView;
  onLearnArea?: LearnAreaFn;
  /** A learn is going: an area due waits for it. Else it is read when a test needs it. */
  waiting?: boolean;
}) {
  const look =
    area.state === "due" && !waiting
      ? { dot: "none" as const, said: "Not learned yet" }
      : LOOK[area.state];
  const quiet = area.state === "due" || area.state === "listed" || area.state === "outside";
  const said =
    area.state === "current" && area.learned_at ? `Learned ${ago(area.learned_at)}` : look.said;
  const asks =
    (area.state === "listed" || area.state === "outside" || (area.state === "due" && !waiting)) &&
    onLearnArea;
  return (
    <li
      aria-label={area.title || area.id}
      data-state={area.state}
      className="flex min-h-[52px] flex-wrap items-center gap-x-3.5 gap-y-1 border-rule px-5 py-2.5 not-last:border-b max-md:px-4"
    >
      <Mark area={area} label={look.said} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className={quiet ? "truncate text-ink-2" : "truncate"}>{area.title || area.id}</span>
        <span className="text-sm text-ink-3">
          {count(area.files, "file")} · {weightText(area)}
        </span>
        {area.state === "failed" && area.reason ? (
          <span className="text-sm text-fail">{area.reason}</span>
        ) : null}
      </span>
      {asks ? (
        <span className="flex flex-wrap items-center justify-end gap-2">
          <Chip>{look.said}</Chip>
          <LearnAreaButton area={area.id} onLearn={onLearnArea} />
        </span>
      ) : area.state === "failed" && onLearnArea ? (
        <span className="flex flex-wrap items-center justify-end gap-2">
          <Chip tone="no">{look.said}</Chip>
          <LearnAreaButton area={area.id} onLearn={onLearnArea} label="Try again" />
        </span>
      ) : (
        <Chip tone={learned(area) ? "ok" : area.state === "failed" ? "no" : "plain"}>{said}</Chip>
      )}
    </li>
  );
}

/** "Stopped after 6 of 9 areas; what was learned is kept.", and "Go on" to learn the rest. */
export function Stopped({
  progress,
  busy,
  onGoOn,
  interrupted = false,
}: {
  progress: LearnStateView;
  busy: boolean;
  onGoOn: () => void;
  /** The server stopped under it. */
  interrupted?: boolean;
}) {
  const done = progress.areas.filter(learned).length;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-[10px] bg-sunk px-3.5 py-2.5 text-sm text-ink-2"
    >
      <span className="min-w-0 flex-1">
        {interrupted ? "The server stopped while it learned: " : "Stopped "}
        after {done} of {count(progress.areas.length, "area")}; what was learned is kept.
      </span>
      <Button variant="secondary" size="sm" disabled={busy} onClick={onGoOn}>
        Go on
      </Button>
    </div>
  );
}

/** What the watch and the profile left out, in a line each: "12 of 35 areas are in the
 * profile" with the ones left out, and how many files the watch didn't take. */
export function LeftOut({
  profile,
  progress,
}: {
  profile: ProfileFit | null;
  progress: LearnStateView | null;
}) {
  const fit = profile?.left_out.length ? profile : null;
  const past = progress?.left_out.past_cap ?? 0;
  const large = progress?.left_out.too_large ?? 0;
  if (!fit && !past && !large) return null;
  return (
    <div className="flex flex-col gap-2" data-testid="left-out">
      <div className="flex flex-wrap items-center gap-1.5">
        {fit ? (
          <Chip tone="warn">
            {fit.areas} of {count(fit.of, "area")} are in the profile
          </Chip>
        ) : null}
        {past ? (
          <Chip
            tone="warn"
            title="At most 2,000 files are watched: name the agent's with code.include"
          >
            {count(past, "file")} past the watch
          </Chip>
        ) : null}
        {large ? <Chip tone="warn">{count(large, "file")} over 1 MB</Chip> : null}
      </div>
      {fit ? (
        <Fold title={`Left out of the profile · ${fit.left_out.length}`}>
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0 px-1">
            {fit.left_out.map((title) => (
              <li key={title}>
                <Chip>{title}</Chip>
              </li>
            ))}
          </ul>
        </Fold>
      ) : null}
    </div>
  );
}

/** The app's areas, heaviest first: what each is, how big, and whether it is learned. An area
 * outside the agent, or listed as weighing nothing, offers "Learn this area"; a failed one says
 * why and offers "Try again" (*onLearnArea*). */
export function LearnedAreas({
  progress,
  busy,
  onGoOn,
  onLearnArea,
  interrupted = false,
}: {
  progress: LearnStateView;
  /** A learn is running: "Go on" waits. */
  busy: boolean;
  onGoOn: () => void;
  onLearnArea?: LearnAreaFn;
  /** The last learn was cut by a server that stopped. */
  interrupted?: boolean;
}) {
  if (!progress.areas.length && progress.state !== "stopped") return null;
  return (
    <section aria-label="Areas" className="flex flex-col gap-3">
      {progress.state === "stopped" ? (
        <Stopped progress={progress} busy={busy} onGoOn={onGoOn} interrupted={interrupted} />
      ) : null}
      {progress.areas.length ? (
        <Card>
          <p className="m-0 border-b border-rule px-5 py-3 text-sm text-ink-2 max-md:px-4">
            {areasSummary(progress.areas)}
          </p>
          <ul className="m-0 list-none p-0">
            {progress.areas.map((area) => (
              <AreaRow
                key={area.id}
                area={area}
                onLearnArea={onLearnArea}
                waiting={busy || progress.state === "learning"}
              />
            ))}
          </ul>
        </Card>
      ) : null}
    </section>
  );
}
