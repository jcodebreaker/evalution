import { Check, MousePointerClick } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { PageOffer, PageReach } from "../../lib/schema";
import { Button } from "../../ui/button";
import { CopyField } from "../../ui/copy-field";
import { Spinner } from "../../ui/loading";
import { TextLink } from "../shared/group";
import { MissingPieceNote } from "../shared/next-step";
import { LearnSteps, oneMessage, ProposedMap, useLearnPoll } from "./LearnFlow";
import { useOpenEnvironment } from "./use-isolation";

/** The demo (plan section 5): it runs from the terminal, so the card gives its command. */
export const DEMO = "uv run --project server evalution demo ui";

/**
 * The offer to test an app through its page, made where the person already is (Connect's last
 * step, a Home card) when its code has a chat page and nothing of it is set up. "Set up UI
 * testing" is the whole flow on this card: the agent learns the page on evalution's copy (its
 * steps as it goes), its rehearsal's screenshots and video show, and one Approve. What comes
 * first, when the page can't be learned yet, is offered here too: Install for a missing browser,
 * Set up testing for the copy. "Not now" turns the offer down for good, on the server.
 */
export function UiOffer({ target, className }: { target: string; className?: string }) {
  const [offer, setOffer] = useState<PageOffer | null>(null);
  const [reach, setReach] = useState<PageReach | null>(null);
  const [working, setWorking] = useState<"setup" | "dismiss" | "undo" | null>(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = useOpenEnvironment();
  const follow = useCallback(async () => {
    try {
      setReach(await api.pageReach(target));
    } catch {
      // Read again at the next tick.
    }
  }, [target]);
  const read = useCallback(async () => {
    try {
      const found = await api.pageOffer(target);
      setOffer(found);
      // Set up earlier, and not done: the card picks it up where it is.
      if (found.underway) await follow();
    } catch {
      setOffer(null);
    }
  }, [target, follow]);
  useEffect(() => {
    setOffer(null);
    setReach(null);
    void read();
  }, [read]);
  useLearnPoll(reach, follow);

  // Once started, the card follows the setup to its end, whatever the offer says now.
  if (!reach && !offer?.offer) return null;

  const setUp = async () => {
    if (!offer?.can_learn) {
      open?.();
      return;
    }
    setWorking("setup");
    setError(null);
    try {
      setReach(await api.learnPage(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't start setting up UI testing."));
    }
    setWorking(null);
  };
  const notNow = async () => {
    setWorking("dismiss");
    setError(null);
    try {
      setOffer(await api.dismissPageOffer(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't save that."));
    }
    setWorking(null);
  };
  const undo = async () => {
    setWorking("undo");
    setError(null);
    try {
      await api.undoPage(target);
      // Back to its API: the offer is made again.
      setReach(null);
      await read();
    } catch (failure) {
      setError(errorText(failure, "Couldn't undo it."));
    }
    setWorking(null);
  };
  const learn = reach?.learn ?? null;
  const approved = reach !== null && reach.plays !== "api" && !reach.proposed;
  const missing = offer?.missing ?? null;
  // A piece to install comes first, with its own button; else Set up UI testing opens the Test
  // environment for what comes first there (the copy).
  const installFirst = missing !== null && missing.part !== "copy";

  return (
    <section
      className={cn(
        "flex flex-col gap-3 text-[14px]",
        className,
        "border-tint/50 shadow-[inset_3px_0_0_var(--color-tint)]",
      )}
      aria-label="UI testing"
      data-testid="ui-offer"
    >
      <div className="flex flex-col gap-1">
        <span className="text-[11.5px] font-semibold tracking-[0.02em] text-ink-3 uppercase">
          New for {target}
        </span>
        <h2 className="m-0 flex items-center gap-2 text-[15px] font-semibold">
          <MousePointerClick className="size-4 text-tint" aria-hidden />
          {target} has a page. Test it the way people use it?
        </h2>
      </div>
      {reach ? (
        <>
          {learn && learn.state !== "done" ? (
            <LearnSteps target={target} learn={learn} onStopped={setReach} />
          ) : null}
          {reach.proposed && learn?.state !== "running" ? (
            <ProposedMap target={target} reach={reach} onChange={setReach} />
          ) : null}
          {approved ? (
            <div className="flex flex-wrap items-center gap-3">
              <p className="m-0 flex items-center gap-1.5" data-testid="ui-approved">
                <Check className="size-4 text-success" aria-hidden />
                Approved. From the next run, tests reach {target} through its page.
              </p>
              {reach.can_undo ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={working !== null}
                  onClick={() => void undo()}
                >
                  {working === "undo" ? <Spinner /> : null}
                  Undo
                </Button>
              ) : null}
            </div>
          ) : null}
          {learn?.state === "failed" ? (
            <div>
              <Button disabled={working !== null} onClick={() => void setUp()}>
                {working === "setup" ? <Spinner /> : null}
                Try again
              </Button>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <p className="m-0 max-w-[68ch] text-ink-2">
            Tests can type each message into its page and check the reply shows, on evalution's
            copy, keeping a screenshot of every message and a video when a try fails.
          </p>
          {offer?.can_learn ? (
            <p className="m-0 max-w-[68ch] text-[13px] text-ink-3">{oneMessage(target)}</p>
          ) : missing && installFirst ? (
            <MissingPieceNote piece={missing} onInstalled={() => void read()} />
          ) : offer?.why_not ? (
            <p className="m-0 max-w-[68ch] text-[13px] text-ink-3">{offer.why_not}</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            {offer?.can_learn || (open && !installFirst) ? (
              <Button disabled={working !== null} onClick={() => void setUp()}>
                {working === "setup" ? <Spinner /> : null}
                Set up UI testing
              </Button>
            ) : null}
            <Button variant="ghost" disabled={working !== null} onClick={() => void notNow()}>
              Not now
            </Button>
            <TextLink
              className="text-[13px]"
              aria-expanded={demo}
              aria-controls="ui-demo"
              onClick={() => setDemo(!demo)}
            >
              Try the demo first
            </TextLink>
          </div>
          {/* The demo runs from the terminal (`evalution demo ui`): the card gives its command. */}
          <div
            id="ui-demo"
            hidden={!demo}
            className="flex max-w-[68ch] flex-col gap-2 text-[13px] text-ink-2"
            data-testid="ui-demo"
          >
            <p className="m-0">
              Plays two tests through a made-up app's page in about a minute, and opens the result
              with its screenshots and video. It spends nothing.
            </p>
            <CopyField value={DEMO} label="The command that starts the demo" />
            <p className="m-0 text-ink-3">Run it in the folder evalution is installed in.</p>
          </div>
        </>
      )}
      {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
    </section>
  );
}
