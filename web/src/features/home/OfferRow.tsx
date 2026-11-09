import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { PageOffer, PageReach } from "../../lib/schema";
import { Button } from "../../ui/button";
import { CopyField } from "../../ui/copy-field";
import { Spinner } from "../../ui/loading";
import { LearnSteps, oneMessage, ProposedMap, useLearnPoll } from "../environment/LearnFlow";
import { DEMO } from "../environment/UiOffer";
import { useOpenEnvironment } from "../environment/use-isolation";
import { TextLink } from "../shared/group";
import { MissingPieceNote } from "../shared/next-step";
import { NeedRow } from "./NeedRow";

/** The UI-testing offer as Home holds it: whether there is one, and the setup it started. */
export type PageOfferState = {
  offer: PageOffer | null;
  reach: PageReach | null;
  setOffer: (offer: PageOffer | null) => void;
  setReach: (reach: PageReach | null) => void;
  /** Read the offer again, and the setup when one is underway. */
  read: () => Promise<void>;
  /** The row shows: there is an offer, or a setup this page follows to its end. */
  shows: boolean;
};

/** The offer to test an app through its page, read as Home opens (the same reads as `UiOffer`). */
export function usePageOffer(target: string): PageOfferState {
  const [offer, setOffer] = useState<PageOffer | null>(null);
  const [reach, setReach] = useState<PageReach | null>(null);
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
      // Set up earlier, and not done: the row picks it up where it is.
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
  return { offer, reach, setOffer, setReach, read, shows: reach !== null || offer?.offer === true };
}

/**
 * The offer as a row of Needs you: "Set up UI testing" is its one action, and the whole flow
 * follows in the row (the agent's steps, its map with the rehearsal's screenshots and video, and
 * one Approve). What comes first when the page can't be learned yet is offered here too: Install
 * for a missing browser, the Test environment for the copy. "Not now" turns it down for good on
 * the server; the demo's command is a line away.
 */
export function OfferRow({ target, state }: { target: string; state: PageOfferState }) {
  const { offer, reach, setOffer, setReach, read } = state;
  const [working, setWorking] = useState<"setup" | "dismiss" | "undo" | null>(null);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = useOpenEnvironment();
  if (!state.shows) return null;

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
  const canSetUp = offer?.can_learn || (open !== null && !installFirst);

  return (
    <NeedRow
      tone="new"
      testId="ui-offer"
      label="UI testing"
      title={`The agent can learn ${target}'s page`}
      line={
        reach ? null : (
          <>
            Then tests type each message there and check the reply shows, on evalution's copy.{" "}
            <TextLink
              className="text-[13px]"
              disabled={working !== null}
              onClick={() => void notNow()}
            >
              Not now
            </TextLink>
            {" · "}
            <TextLink
              className="text-[13px]"
              aria-expanded={demo}
              aria-controls="ui-demo"
              onClick={() => setDemo(!demo)}
            >
              Try the demo first
            </TextLink>
          </>
        )
      }
      action={
        !reach && canSetUp ? (
          <Button
            variant="secondary"
            size="sm"
            disabled={working !== null}
            onClick={() => void setUp()}
          >
            {working === "setup" ? <Spinner /> : null}
            Set up UI testing
          </Button>
        ) : null
      }
    >
      {reach ? (
        <div className="flex flex-col gap-3">
          {learn && learn.state !== "done" ? (
            <LearnSteps target={target} learn={learn} onStopped={setReach} />
          ) : null}
          {reach.proposed && learn?.state !== "running" ? (
            <ProposedMap target={target} reach={reach} onChange={setReach} />
          ) : null}
          {approved ? (
            <div className="flex flex-wrap items-center gap-3">
              <p className="m-0" data-testid="ui-approved">
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
              <Button
                variant="secondary"
                size="sm"
                disabled={working !== null}
                onClick={() => void setUp()}
              >
                {working === "setup" ? <Spinner /> : null}
                Try again
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <>
          {offer?.can_learn ? (
            <p className="m-0 max-w-[68ch] text-[13px] text-ink-3">{oneMessage(target)}</p>
          ) : missing && installFirst ? (
            <MissingPieceNote piece={missing} onInstalled={() => void read()} />
          ) : offer?.why_not ? (
            <p className="m-0 max-w-[68ch] text-[13px] text-ink-3">{offer.why_not}</p>
          ) : null}
          {/* The demo runs from the terminal (`evalution demo ui`): the row gives its command. */}
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
    </NeedRow>
  );
}
