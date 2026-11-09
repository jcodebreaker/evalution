import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { Keep, KeptPageMap, Proof, PageReach as Reach } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { MissingPieceNote, PageShot } from "../shared/next-step";
import { ProofControl, proofWords } from "../shared/ProofControl";
import { LearnSteps, MapWords, oneMessage, ProposedMap, Shots, useLearnPoll } from "./LearnFlow";

type Way = "api" | "page";

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
        tone: "text-fail",
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

/**
 * The step "How tests reach <target>": through its API, or through its own page by a map the
 * test writer learned from the code and the page. Learning it is one flow on this card: the
 * agent's steps as it goes, then the map it tried on evalution's copy in plain words with its
 * rehearsal's screenshots and video, and one Approve. A map a commit left out of date, or one the
 * check before a run found broken, offers to learn the page again.
 */
export function PageReach({ target }: { target: string }) {
  const [reach, setReach] = useState<Reach | null>(null);
  const [failed, setFailed] = useState(false);
  const [way, setWay] = useState<Way | null>(null);
  const [working, setWorking] = useState<"undo" | "learn" | "proof" | null>(null);
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
  useLearnPoll(reach, read);

  if (!reach) {
    return failed ? (
      <p className="m-0 text-[14px] text-ink-2">Couldn't load how tests reach {target}.</p>
    ) : null;
  }
  // Reached through its page alone: there is no API way to show.
  const only = reach.plays === "page_only";
  const learning = reach.learn?.state === "running";
  const shown: Way = only
    ? "page"
    : (way ?? (reach.plays === "page" || reach.proposed || reach.learn ? "page" : "api"));

  const undo = async () => {
    setWorking("undo");
    setError(null);
    try {
      setReach(await api.undoPage(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't undo it."));
    }
    setWorking(null);
  };
  const prove = async (part: keyof Proof, keep: Keep) => {
    setWorking("proof");
    setError(null);
    try {
      setReach(await api.setPageProof(target, { [part]: keep }));
    } catch (failure) {
      setError(errorText(failure, "Couldn't change what each try keeps."));
    }
    setWorking(null);
  };
  const learn = async () => {
    setWorking("learn");
    setError(null);
    try {
      setReach(await api.learnPage(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't start learning the page."));
    }
    setWorking(null);
  };
  const current = reach.current;
  const again = current?.status === "stale" || current?.status === "broken";

  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-[12px] border border-separator bg-group p-5 text-[14px] max-md:p-4"
      aria-label={`How tests reach ${target}`}
      data-testid="page-reach"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="m-0 text-[15px] font-semibold">How tests reach {target}</h3>
        {only ? (
          <span className="ms-auto text-[13px] text-ink-2" data-testid="page-only">
            Its page only: no API under it, so a pass is reply only
          </span>
        ) : null}
        <fieldset
          hidden={only}
          className="m-0 ms-auto flex gap-0.5 rounded-[7px] border-0 bg-sunk-2 p-[3px]"
          aria-label="How tests reach it"
        >
          {(["api", "page"] as const).map((each) => (
            <button
              key={each}
              type="button"
              aria-pressed={shown === each}
              onClick={() => setWay(each)}
              className={cn(
                "h-[26px] rounded-[5px] px-2.5 text-[13px]",
                shown === each
                  ? "bg-group font-medium text-ink shadow-[0_1px_2px_rgba(20,22,43,0.14)]"
                  : "text-ink-2",
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
          {reach.learn && reach.learn.state !== "done" ? (
            <LearnSteps target={target} learn={reach.learn} onStopped={setReach} />
          ) : null}

          {reach.proposed && !learning ? (
            <ProposedMap target={target} reach={reach} onChange={setReach} undoHere />
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
          ) : !reach.proposed && !reach.learn ? (
            <p className="m-0 max-w-[68ch] text-ink-2">
              The agent learns {target}'s page from its code and from the page itself, on
              evalution's copy: where the message box, Send and the replies are.
            </p>
          ) : null}

          {reach.proof && reach.plays !== "api" ? (
            <div className="flex flex-col gap-2" data-testid="page-proof">
              <p className="m-0 font-medium">What each try keeps</p>
              <ProofControl
                proof={reach.proof}
                disabled={!reach.writable || working !== null}
                onChange={(part, keep) => void prove(part, keep)}
              />
              <p className="m-0 max-w-[68ch] text-[13px] text-ink-3">
                {reach.writable
                  ? `${proofWords(reach.proof)} A test can say otherwise for itself, under What it checks.`
                  : `${target}'s page: block is in evalution.yaml: change proof there.`}
              </p>
            </div>
          ) : null}

          {!reach.can_learn && reach.missing ? (
            <MissingPieceNote piece={reach.missing} onInstalled={() => void read()} />
          ) : !reach.can_learn && reach.why_not ? (
            <p className="m-0 max-w-[68ch] text-[13px] text-ink-3">{reach.why_not}</p>
          ) : null}
          {reach.problem ? <p className="m-0 text-fail">{reach.problem}</p> : null}
          {learning || (reach.proposed && !reach.can_undo) ? null : (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-3">
                {/* A map waiting says Re-learn the page beside its Approve. */}
                {reach.proposed ? null : (
                  <Button
                    variant={again || !current ? "default" : "secondary"}
                    disabled={!reach.can_learn || working !== null}
                    onClick={() => void learn()}
                  >
                    {working === "learn" ? <Spinner /> : null}
                    {again ? "Re-learn the page" : current ? "Learn it again" : "Set up UI testing"}
                  </Button>
                )}
                {reach.can_undo ? (
                  <Button variant="ghost" disabled={working !== null} onClick={() => void undo()}>
                    Undo the last map
                  </Button>
                ) : null}
              </div>
              {reach.can_learn && !reach.proposed ? (
                <p className="m-0 max-w-[68ch] text-[12.5px] text-ink-3">{oneMessage(target)}</p>
              ) : null}
            </div>
          )}
        </>
      )}
      {error ? <p className="m-0 text-[13px] text-fail">{error}</p> : null}
    </section>
  );
}
