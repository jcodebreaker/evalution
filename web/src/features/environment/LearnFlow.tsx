import { Check, RotateCcw, Square } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import type { KeptPageMap, PageLearn, PageReach } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { PictureDialog } from "../../ui/dialog";
import { Spinner } from "../../ui/loading";
import { Group } from "../shared/group";
import { Checklist } from "./Checklist";

/** How often the page reads again while the agent learns the page. */
export const LEARN_POLL_MS = 1_000;

/** What Set up UI testing tells the person before the agent starts: their yes to one message. */
export function oneMessage(target: string): string {
  return `The agent learns ${target}'s page on evalution's copy, then tries its map with one message there. You approve the map once, after its screenshots and video.`;
}

/** Reads `read` every second while the agent learns the page. */
export function useLearnPoll(reach: PageReach | null, read: () => unknown) {
  useVisiblePoll(read, LEARN_POLL_MS, reach?.learn?.state === "running");
}

/** A page map in plain words: each entry, and where it is on the page. */
export function MapWords({ kept }: { kept: KeptPageMap }) {
  if (kept.error) {
    return <p className="m-0 text-[13px] text-fail">The map doesn't read: {kept.error}</p>;
  }
  return (
    <Group>
      {kept.map.map((entry) => (
        <div
          key={entry.what}
          className="flex min-w-0 items-baseline gap-3 border-separator px-3.5 py-2 not-first:border-t"
        >
          <span className="w-40 shrink-0 text-ink-2 max-md:w-28">{entry.what}</span>
          <span className="min-w-0 break-words">{entry.where}</span>
        </div>
      ))}
    </Group>
  );
}

/** Entries of a map that are how it waits and what it keeps, not a part found on the page. */
const SETTINGS = new Set([
  "Done when",
  "Waits for the app to take a message",
  "Waits for the reply",
  "Screenshots",
  "Video",
]);

/** A page map's parts as chips, each with where it is on the page, then how it waits and what it
 * keeps in one line. */
export function MapChips({ kept }: { kept: KeptPageMap }) {
  if (kept.error) {
    return <p className="m-0 text-[13px] text-fail">The map doesn't read: {kept.error}</p>;
  }
  const parts = kept.map.filter((entry) => !SETTINGS.has(entry.what));
  const settings = kept.map.filter((entry) => SETTINGS.has(entry.what));
  return (
    <div className="flex flex-col gap-2">
      <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label="What it found">
        {parts.map((entry) => (
          <li
            key={entry.what}
            className="inline-flex max-w-full min-w-0 items-baseline gap-1.5 rounded-full bg-group px-2.5 py-1 text-[12.5px] ring-1 ring-separator"
          >
            <span className="shrink-0 font-medium">{entry.what}</span>
            <span
              className="min-w-0 truncate font-mono text-[11.5px] text-ink-3"
              title={entry.where}
            >
              {entry.where}
            </span>
          </li>
        ))}
      </ul>
      {settings.length ? (
        <p className="m-0 max-w-[68ch] text-[12.5px] text-ink-3">
          {settings.map((entry) => `${entry.what}: ${entry.where}`).join(" · ")}
        </p>
      ) : null}
    </div>
  );
}

/** The rehearsal's screenshots, small, each opening large. */
export function Shots({ shots }: { shots: string[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!shots.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {shots.map((shot, index) => (
        <button
          key={shot}
          type="button"
          aria-label={`Enlarge screenshot ${index + 1} of the rehearsal`}
          onClick={() => setOpen(shot)}
          className="overflow-hidden rounded-lg ring-1 ring-separator hover:ring-tint focus-visible:outline-2 focus-visible:outline-tint"
        >
          <img
            src={shot}
            alt={`The page after the rehearsal's message, screenshot ${index + 1}`}
            loading="lazy"
            className="block h-[96px] w-[156px] object-cover object-top"
          />
        </button>
      ))}
      {open ? (
        <PictureDialog
          onClose={() => setOpen(null)}
          title="The page after the rehearsal's message"
          src={open}
          alt="The page after the rehearsal's message"
        />
      ) : null}
    </div>
  );
}

/** The rehearsal's video: the one message typed, sent and answered on the page. */
export function RehearsalVideo({ src }: { src: string }) {
  return (
    <video
      src={src}
      controls
      muted
      playsInline
      preload="metadata"
      aria-label="A video of the rehearsal on the page"
      className="block w-full max-w-[560px] rounded-lg bg-black ring-1 ring-separator"
    />
  );
}

/**
 * The agent's map waiting for approval, as the person approves it: each part it found and where,
 * what its rehearsal showed, its screenshots and video, then Approve or Re-learn the page.
 * `onChange`: the view after either. `undoHere`: the card offers Undo once it's approved (Test
 * environment's); else the words say where it is.
 */
export function ProposedMap({
  target,
  reach,
  onChange,
  undoHere = false,
}: {
  target: string;
  reach: PageReach;
  onChange: (reach: PageReach) => void;
  undoHere?: boolean;
}) {
  const [working, setWorking] = useState<"approve" | "learn" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const kept = reach.proposed;
  if (!kept) return null;
  const act = async (which: "approve" | "learn") => {
    setWorking(which);
    setError(null);
    try {
      onChange(await (which === "approve" ? api.approvePage(target) : api.learnPage(target)));
    } catch (failure) {
      setError(
        errorText(
          failure,
          which === "approve" ? "Couldn't approve it." : "Couldn't start learning the page.",
        ),
      );
    }
    setWorking(null);
  };
  const undo = undoHere ? "Undo later from this card." : "Undo later under Test environment.";
  return (
    <div className="flex flex-col gap-3" data-testid="page-map-proposed">
      <div className="flex flex-col gap-1">
        <p className="m-0 font-medium">Approve how tests use the page</p>
        <p className="m-0 max-w-[68ch] text-ink-2">
          The agent tried this map once, on evalution's copy. Look at what it did, then approve.
          Tests won't play through the page until you do.
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[13px] font-medium text-ink-2">What it found</span>
        <MapChips kept={kept} />
      </div>
      {kept.report ? <p className="m-0 max-w-[68ch] text-ink-2">{kept.report}</p> : null}
      <Shots shots={kept.shots} />
      {kept.video ? <RehearsalVideo src={kept.video} /> : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!reach.writable || working !== null} onClick={() => void act("approve")}>
          {working === "approve" ? <Spinner /> : <Check />}
          Approve
        </Button>
        <Button
          variant="secondary"
          disabled={!reach.can_learn || working !== null}
          title={reach.can_learn ? oneMessage(target) : (reach.why_not ?? undefined)}
          onClick={() => void act("learn")}
        >
          {working === "learn" ? <Spinner /> : <RotateCcw />}
          Re-learn the page
        </Button>
        <span className="min-w-0 flex-1 text-[12.5px] text-ink-3">
          {reach.writable
            ? reach.managed
              ? `From the next run, tests reach it through its page. ${undo}`
              : `From the next run, tests reach it through its page. evalution.yaml isn't changed: the map is kept in the data folder. ${undo}`
            : `${target}'s page: block in evalution.yaml wins: change the map there, or take the block out to use this one.`}
        </span>
      </div>
      {error ? <p className="m-0 text-[13px] text-fail">{error}</p> : null}
    </div>
  );
}

/** The agent learning the page, as it goes: its steps, Stop, and what went wrong if it did. */
export function LearnSteps({
  target,
  learn,
  onStopped,
}: {
  target: string;
  learn: PageLearn;
  onStopped: (reach: PageReach) => void;
}) {
  const [stopping, setStopping] = useState(false);
  const stop = async () => {
    setStopping(true);
    try {
      onStopped(await api.stopLearningPage(target));
    } finally {
      setStopping(false);
    }
  };
  return (
    <div className="flex flex-col gap-2.5" data-testid="page-learn">
      {learn.state === "running" ? (
        <div className="flex flex-col gap-0.5">
          <p className="m-0 font-medium">The agent is learning {target}'s page</p>
          <p className="m-0 max-w-[68ch] text-ink-2">
            On evalution's copy: it reads the page's code, looks at the page, and tries one message.
          </p>
        </div>
      ) : null}
      <Checklist steps={learn.steps} />
      {learn.state === "failed" && learn.error ? (
        <p className="m-0 max-w-[68ch] text-[13px] text-warning">
          {learn.error} The whole conversation is kept among the agent's chats.
        </p>
      ) : null}
      {learn.state === "running" ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" disabled={stopping} onClick={() => void stop()}>
            {stopping ? <Spinner /> : <Square />}
            Stop
          </Button>
          <span className="text-[12.5px] text-ink-3">
            Underway on its own: you can leave this page and come back to it.
          </span>
        </div>
      ) : null}
    </div>
  );
}
