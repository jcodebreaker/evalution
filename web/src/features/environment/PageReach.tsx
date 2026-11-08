import { Check } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { KeptPageMap, PageReach as Reach } from "../../lib/schema";
import { Button } from "../../ui/button";
import { PictureDialog } from "../../ui/dialog";
import { Spinner } from "../../ui/loading";
import { Group } from "../shared/group";
import { MissingPieceNote, PageShot, relearnWords } from "../shared/next-step";

type Way = "api" | "page";

/** What the test writer is asked, to learn the page, or to learn it again. */
export function learnPage(target: string, reach: Reach | null): string {
  const current = reach?.current;
  if (current?.status === "broken") {
    return relearnWords(target, current.missing);
  }
  if (current?.status === "stale") {
    return `The page map of ${target} is out of date: a commit changed ${current.stale_files.join(", ") || "a file it rests on"}. Learn the page again and propose a new map.`;
  }
  return `Learn how tests reach ${target}'s page: read its page code, look at it on evalution's copy, and propose a page map.`;
}

/** How a kept map stands, in a sentence. */
function standing(target: string, kept: KeptPageMap): { text: string; tone: string } {
  switch (kept.status) {
    case "stale":
      return {
        text: `Out of date: a commit changed ${kept.stale_files.join(", ") || "a file it rests on"}. The next run checks it on evalution's copy first, without sending a message.`,
        tone: "text-warning",
      };
    case "broken":
      return {
        text: `It no longer matches the page: ${target}'s page has no ${kept.missing.join(", ")}. Runs through the page stop until it's learned again.`,
        tone: "text-destructive",
      };
    case "by_hand":
      return { text: "Written by hand in the target's page: block.", tone: "text-ink-2" };
    case "tried":
      return {
        text: "The agent's map, tried with one message on evalution's copy.",
        tone: "text-ink-2",
      };
    default:
      return {
        text: "Tests type and click on the page as a person would, on evalution's copy.",
        tone: "text-ink-2",
      };
  }
}

function MapWords({ kept }: { kept: KeptPageMap }) {
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

function Shots({ shots }: { shots: string[] }) {
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

/**
 * The step "How tests reach <target>": through its API, or through its own page by a map the
 * test writer learned from the code and the page. A map tried on evalution's copy shows in plain
 * words with its rehearsal's screenshots and one Approve button; a map a commit left out of date,
 * or one the check before a run found broken, offers to learn the page again. `onAsk` hands the
 * test writer a message; without it, there is no button to learn.
 */
export function PageReach({
  target,
  onAsk,
}: {
  target: string;
  onAsk?: (text: string) => void | Promise<void>;
}) {
  const [reach, setReach] = useState<Reach | null>(null);
  const [failed, setFailed] = useState(false);
  const [way, setWay] = useState<Way | null>(null);
  const [working, setWorking] = useState<"approve" | "undo" | "ask" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const read = useCallback(async () => {
    try {
      setReach(await api.pageReach(target));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [target]);
  useEffect(() => {
    setReach(null);
    setWay(null);
    void read();
  }, [read]);

  if (!reach) {
    return failed ? (
      <p className="m-0 text-[13px] text-ink-2">Couldn't load how tests reach {target}.</p>
    ) : null;
  }
  // Reached through its page alone: there is no API way to show.
  const only = reach.plays === "page_only";
  const shown: Way = only
    ? "page"
    : (way ?? (reach.plays === "page" || reach.proposed ? "page" : "api"));

  const act = async (what: "approve" | "undo") => {
    setWorking(what);
    setError(null);
    try {
      setReach(what === "approve" ? await api.approvePage(target) : await api.undoPage(target));
    } catch (failure) {
      setError(
        errorText(failure, what === "approve" ? "Couldn't approve it." : "Couldn't undo it."),
      );
    }
    setWorking(null);
  };
  const ask = async () => {
    if (!onAsk) return;
    setWorking("ask");
    try {
      await onAsk(learnPage(target, reach));
    } finally {
      setWorking(null);
    }
  };
  const current = reach.current;
  const again = current?.status === "stale" || current?.status === "broken";
  const learnButton = onAsk ? (
    <Button
      variant={again || (!current && !reach.proposed) ? "default" : "secondary"}
      disabled={!reach.can_learn || working !== null}
      onClick={() => void ask()}
    >
      {working === "ask" ? <Spinner /> : null}
      {again
        ? "Re-learn the page"
        : current || reach.proposed
          ? "Learn it again"
          : "Learn the page"}
    </Button>
  ) : null;

  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-[14px] border border-border bg-group p-5 text-[13px] max-md:p-4"
      aria-label={`How tests reach ${target}`}
      data-testid="page-reach"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="m-0 text-[15px] font-semibold">How tests reach {target}</h3>
        {only ? (
          <span className="ms-auto text-[12.5px] text-ink-2" data-testid="page-only">
            Its page only: no API under it, so a pass is reply only
          </span>
        ) : null}
        <fieldset
          hidden={only}
          className="m-0 ms-auto flex gap-0.5 rounded-lg border-0 bg-bubble p-0.5"
          aria-label="How tests reach it"
        >
          {(["api", "page"] as const).map((each) => (
            <button
              key={each}
              type="button"
              aria-pressed={shown === each}
              onClick={() => setWay(each)}
              className={cn(
                "rounded-md px-2.5 py-1 text-[12.5px]",
                shown === each ? "bg-group text-foreground shadow-sm" : "text-ink-2",
              )}
            >
              {each === "api" ? "Its API" : "Its page"}
            </button>
          ))}
        </fieldset>
      </div>

      {shown === "api" ? (
        <p className="m-0 max-w-[68ch] text-ink-2">
          {reach.plays === "api"
            ? `Tests send each message through ${target}'s API and read the reply it returns.`
            : `Tests reach ${target} through its page now. Its API still sends each message; the page shows the same turn, and what showed is checked too.`}
        </p>
      ) : (
        <>
          {reach.proposed ? (
            <div className="flex flex-col gap-2.5" data-testid="page-map-proposed">
              <p className="m-0 font-medium">The agent's map, waiting for your approval</p>
              <MapWords kept={reach.proposed} />
              {reach.proposed.report ? (
                <p className="m-0 max-w-[68ch] text-ink-2">{reach.proposed.report}</p>
              ) : null}
              <Shots shots={reach.proposed.shots} />
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  disabled={!reach.managed || working !== null}
                  onClick={() => void act("approve")}
                >
                  {working === "approve" ? <Spinner /> : <Check />}
                  Approve
                </Button>
                <span className="min-w-0 flex-1 text-[12px] text-ink-3">
                  {reach.managed
                    ? "From the next run, tests reach it through its page."
                    : `${target} is set in evalution.yaml: add the map to its page: block there.`}
                </span>
              </div>
            </div>
          ) : null}

          {current ? (
            <div className="flex flex-col gap-2.5" data-testid="page-map-current">
              {reach.proposed ? <p className="m-0 font-medium">The map in use</p> : null}
              <p className={cn("m-0 max-w-[68ch]", standing(target, current).tone)}>
                {standing(target, current).text}
              </p>
              {current.status === "broken" && current.missing_shot ? (
                <PageShot
                  src={current.missing_shot}
                  title={`${target}'s page, without ${current.missing.join(", ")}`}
                  alt={`${target}'s page as the check found it`}
                />
              ) : null}
              <MapWords kept={current} />
              <Shots shots={reach.proposed ? [] : current.shots} />
            </div>
          ) : !reach.proposed ? (
            <p className="m-0 max-w-[68ch] text-ink-2">
              The agent learns {target}'s page from its code and from the page itself, on
              evalution's copy: where the message box, Send and the replies are. Trying the map
              sends one message, after you say yes in the chat.
            </p>
          ) : null}

          {!reach.can_learn && reach.missing ? (
            <MissingPieceNote piece={reach.missing} />
          ) : !reach.can_learn && reach.why_not ? (
            <p className="m-0 max-w-[68ch] text-[12.5px] text-ink-3">{reach.why_not}</p>
          ) : null}
          {reach.problem ? <p className="m-0 text-destructive">{reach.problem}</p> : null}
          <div className="flex flex-wrap items-center gap-3">
            {learnButton}
            {reach.can_undo ? (
              <Button variant="ghost" disabled={working !== null} onClick={() => void act("undo")}>
                Undo the last map
              </Button>
            ) : null}
          </div>
        </>
      )}
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
    </section>
  );
}
