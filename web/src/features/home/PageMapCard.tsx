import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Health, PageReach } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { LearnSteps, oneMessage, ProposedMap, useLearnPoll } from "../environment/LearnFlow";
import { useOpenEnvironment } from "../environment/use-isolation";
import { TextLink } from "../shared/group";
import { PageShot } from "../shared/next-step";
import { NeedRow } from "./NeedRow";

/**
 * Home's word on the page map, a row of Needs you: only when a commit changed a file its evidence
 * cites, or the check before a run (or a try) found the page without what it names, shown as it
 * was then. Re-learn the page is the row's one action, and its flow follows in the row, as Set up
 * UI testing's does: the agent's steps as it goes, then its map with the rehearsal's screenshots
 * and video, and one Approve.
 */
export function PageMapCard({ target, page }: { target: string; page: Health["page"] }) {
  const open = useOpenEnvironment();
  const [reach, setReach] = useState<PageReach | null>(null);
  const [working, setWorking] = useState(false);
  /** Re-learn was pressed here: a failed setup is this card's to say. */
  const [mine, setMine] = useState(false);
  /** Its new map was approved here. */
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const follow = useCallback(async () => {
    try {
      setReach(await api.pageReach(target));
    } catch {
      // Read again at the next tick.
    }
  }, [target]);
  const status = page?.status ?? null;
  useEffect(() => {
    setReach(null);
    // A re-learn started earlier, here or on Test environment: the card picks it up.
    if (status) void follow();
  }, [status, follow]);
  useLearnPoll(reach, follow);

  const learn = reach?.learn ?? null;
  const running = learn?.state === "running";
  const failed = mine && learn?.state === "failed";
  const waiting = !done && reach?.proposed != null && !running;
  if (!page && !running && !waiting && !done) return null;
  const broken = page?.status === "broken";

  const relearn = async () => {
    setWorking(true);
    setMine(true);
    setError(null);
    try {
      setReach(await api.learnPage(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't start learning the page."));
    }
    setWorking(false);
  };

  const title =
    page && !running && !waiting && !done
      ? broken
        ? `Tests can't reach ${target} through its page`
        : "The page map is out of date"
      : `How tests reach ${target}`;
  const idle = page !== null && !running && !waiting && !done;

  return (
    <NeedRow
      tone={broken ? "fail" : "unjudged"}
      testId="page-map-card"
      label={`How tests reach ${target}`}
      title={title}
      line={
        idle ? (
          <>
            {broken
              ? `The page map no longer matches ${target}'s page: it has no ${page.missing.join(", ")}. Runs through the page stop until it's learned again.`
              : `A commit changed ${page.stale_files.join(", ") || "a file it rests on"}. The next run checks it on evalution's copy first, without sending a message.`}
            {open ? (
              <>
                {" "}
                <TextLink className="text-[13px]" onClick={open}>
                  Details
                </TextLink>
              </>
            ) : null}
          </>
        ) : null
      }
      action={
        idle ? (
          <Button variant="secondary" size="sm" disabled={working} onClick={() => void relearn()}>
            {working ? <Spinner /> : null}
            {failed ? "Try again" : "Re-learn the page"}
          </Button>
        ) : null
      }
    >
      {learn && (running || failed) ? (
        <LearnSteps target={target} learn={learn} onStopped={setReach} />
      ) : null}
      {waiting && reach ? (
        <ProposedMap
          target={target}
          reach={reach}
          onChange={(next) => {
            setReach(next);
            if (!next.proposed && next.plays !== "api") setDone(true);
          }}
        />
      ) : null}
      {done ? (
        <p className="m-0" data-testid="page-map-approved">
          Approved. From the next run, tests reach {target} through its page again.
        </p>
      ) : null}
      {idle && broken && page.shot ? (
        <PageShot
          src={page.shot}
          title={`${target}'s page, without ${page.missing.join(", ")}`}
          alt={`${target}'s page as the check found it`}
        />
      ) : null}
      {idle ? (
        <p className="m-0 max-w-[68ch] text-[12.5px] text-ink-3">{oneMessage(target)}</p>
      ) : null}
      {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
    </NeedRow>
  );
}
