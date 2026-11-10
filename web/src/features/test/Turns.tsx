import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Actual, Compare, StepPage } from "../../lib/schema";
import { checkWord, notTakenOf, notTakenWords } from "../../lib/verdict";
import type { Outcome } from "../../ui/status";
import { Status } from "../../ui/status";
import { Card } from "./Card";
import { splitAt, turnShown, turnsOf } from "./try-words";

const CELL =
  "min-w-0 px-5 py-4 not-first:border-separator md:not-first:border-s max-md:not-first:border-t max-md:not-first:border-dashed";
const LABEL = "mb-1.5 text-[13px] font-medium text-ink-3 md:hidden";
const TEXT = "m-0 max-h-[280px] overflow-auto text-sm break-words whitespace-pre-wrap";

/** A turn through the API, in a few words with its mark. */
function turnSaid(actual: Actual | undefined): { state: Outcome; text: string } | null {
  if (!actual) return null;
  const text = actual.text.trim() || checkWord(actual);
  const said = text.charAt(0).toUpperCase() + text.slice(1);
  return { state: actual.unjudged ? "unjudged" : actual.passed ? "pass" : "fail", text: said };
}

/** The reply, with the words the page didn't show marked. */
function Answered({ reply, missing }: { reply: string; missing: string | null }) {
  const split = missing ? splitAt(reply, missing) : null;
  if (!split) return <>{reply}</>;
  return (
    <>
      {split.before}
      <mark className="rounded-[3px] bg-fail-soft px-0.5 text-inherit underline decoration-fail decoration-wavy decoration-1 underline-offset-[3px]">
        {split.words}
      </mark>
      {split.after}
    </>
  );
}

/** What the page showed of one message: its text, else what showed instead or why nothing did. */
function Showed({ page }: { page: StepPage }) {
  const text = (page.shown ?? page.instead).trim();
  const failed = page.check?.outcome === "failed";
  const why = page.check?.why.trim();
  return (
    <>
      {text ? (
        <p className={TEXT}>{text}</p>
      ) : (
        <p className="m-0 text-sm text-ink-2">Nothing showed</p>
      )}
      {page.part ? (
        <p className="m-0 mt-2 text-sm text-ink-3" data-testid="page-part">
          {"Shown on the page: "}
          {page.part.shown ? <code className="font-mono">{page.part.shown}</code> : "none of it"}
          {" · Missing: "}
          <code className="font-mono">{page.part.missing}</code>
        </p>
      ) : (failed || page.check?.outcome === "unjudged") && why ? (
        <p className="m-0 mt-2 text-sm text-ink-3">{why.charAt(0).toUpperCase() + why.slice(1)}</p>
      ) : null}
    </>
  );
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={CELL}>
      <div className={LABEL}>{label}</div>
      {children}
    </div>
  );
}

/**
 * Each message of the try, turn by turn: what the person said, what the app answered and, played
 * through the page, what the page showed, with the reply's words it didn't show marked.
 */
export function Turns({ shown }: { shown: Compare }) {
  const turns = turnsOf(shown.steps);
  if (!turns.length) return null;
  const onPage = !!shown.page;
  const notTaken = notTakenOf(shown);
  const grid = onPage ? "md:grid-cols-3" : "md:grid-cols-2";
  const heads = ["The person said", "The app answered", ...(onPage ? ["The page showed"] : [])];
  const marked = turns.some(({ step }) => step.page?.part);
  return (
    <Card title="Turn by turn" testId="turns" aside={marked ? "Missing words are marked" : null}>
      <div
        className={cn(
          "mt-3 grid border-t border-separator bg-sunk text-[13px] font-medium text-ink-3 max-md:hidden",
          grid,
        )}
        aria-hidden
      >
        {heads.map((head) => (
          <div key={head} className="px-5 py-2">
            {head}
          </div>
        ))}
      </div>
      <ol className="m-0 list-none p-0 max-md:mt-3">
        {turns.map(({ step, n }) => {
          const mark = onPage && step.page ? turnShown(step.page) : turnSaid(step.actual);
          const reply = step.actual?.reply ?? "";
          return (
            <li key={n} aria-label={`Turn ${n}`}>
              <div
                className={cn(
                  "flex items-center gap-2 border-t border-separator px-5 py-2 text-sm font-semibold text-ink-2",
                  mark?.state === "fail" && "bg-fail-soft",
                )}
              >
                {`Turn ${n}`}
                <span className="flex-1" />
                {mark ? (
                  <Status state={mark.state} className="text-sm font-normal">
                    {mark.text}
                  </Status>
                ) : null}
              </div>
              <div className={cn("grid border-t border-separator", grid)}>
                <Cell label="The person said">
                  {step.message ? (
                    <p className={TEXT}>{step.message}</p>
                  ) : (
                    <p className="m-0 text-sm text-ink-2">{step.title || "A message"}</p>
                  )}
                </Cell>
                <Cell label="The app answered">
                  {reply ? (
                    <p
                      className={cn(
                        TEXT,
                        "rounded-[7px] bg-sunk px-3 py-2 font-mono text-sm leading-relaxed",
                      )}
                    >
                      <Answered reply={reply} missing={step.page?.part?.missing ?? null} />
                    </p>
                  ) : (
                    <p className="m-0 text-sm text-ink-2">
                      {notTaken
                        ? `No reply: ${notTakenWords(notTaken).toLowerCase()}.`
                        : "No reply"}
                    </p>
                  )}
                </Cell>
                {onPage ? (
                  <Cell label="The page showed">
                    {step.page ? (
                      <Showed page={step.page} />
                    ) : (
                      <p className="m-0 text-sm text-ink-2">Not checked on the page</p>
                    )}
                  </Cell>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
