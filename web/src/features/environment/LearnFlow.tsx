import { Check, Square } from "lucide-react";
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
    return <p className="m-0 text-[12.5px] text-destructive">The map doesn't read: {kept.error}</p>;
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
          className="overflow-hidden rounded-lg ring-[0.5px] ring-separator hover:ring-tint focus-visible:outline-2 focus-visible:outline-ring"
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
      className="block w-full max-w-[560px] rounded-lg bg-black ring-[0.5px] ring-separator"
    />
  );
}

/**
 * The agent's map waiting for approval: in plain words, what its rehearsal showed, its screenshots
 * and video, and one Approve. `onApproved`: the view after it was saved.
 */
export function ProposedMap({
  target,
  reach,
  onApproved,
}: {
  target: string;
  reach: PageReach;
  onApproved: (reach: PageReach) => void;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const kept = reach.proposed;
  if (!kept) return null;
  const approve = async () => {
    setWorking(true);
    setError(null);
    try {
      onApproved(await api.approvePage(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't approve it."));
    }
    setWorking(false);
  };
  return (
    <div className="flex flex-col gap-2.5" data-testid="page-map-proposed">
      <p className="m-0 font-medium">The agent's map, waiting for your approval</p>
      <MapWords kept={kept} />
      {kept.report ? <p className="m-0 max-w-[68ch] text-ink-2">{kept.report}</p> : null}
      {kept.video ? <RehearsalVideo src={kept.video} /> : null}
      <Shots shots={kept.shots} />
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!reach.writable || working} onClick={() => void approve()}>
          {working ? <Spinner /> : <Check />}
          Approve
        </Button>
        <span className="min-w-0 flex-1 text-[12px] text-ink-3">
          {reach.writable
            ? reach.managed
              ? "From the next run, tests reach it through its page."
              : "From the next run, tests reach it through its page. evalution.yaml isn't changed: the map is kept in the data folder."
            : `${target}'s page: block in evalution.yaml wins: change the map there, or take the block out to use this one.`}
        </span>
      </div>
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
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
        <p className="m-0 font-medium">The agent is learning {target}'s page</p>
      ) : null}
      <Checklist steps={learn.steps} />
      {learn.state === "failed" && learn.error ? (
        <p className="m-0 max-w-[68ch] text-[12.5px] text-warning">
          {learn.error} The whole conversation is kept among the agent's chats.
        </p>
      ) : null}
      {learn.state === "running" ? (
        <div>
          <Button variant="ghost" size="sm" disabled={stopping} onClick={() => void stop()}>
            {stopping ? <Spinner /> : <Square />}
            Stop
          </Button>
        </div>
      ) : null}
    </div>
  );
}
