import { useEffect, useState } from "react";
import type { Scope } from "../../lib/api";
import { ApiError, api, errorText, missingOf } from "../../lib/api";
import { cn } from "../../lib/cn";
import { count } from "../../lib/format";
import { about } from "../../lib/isolation";
import type { Estimate, MissingPiece, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";
import { useOpenEnvironment } from "../environment/use-isolation";
import { TextLink } from "../shared/group";
import { HowIsolationLink } from "../shared/HowIsolationWorks";
import { MissingPieceNote } from "../shared/next-step";

/** "How this works": the Test environment screen, or the explainer where the page can't move. */
function HowItWorks() {
  const open = useOpenEnvironment();
  return open ? <TextLink onClick={open}>How this works</TextLink> : <HowIsolationLink />;
}

/** What a run sends, and how its tries are kept apart, in two or three short lines. */
export function IsolationLines({ estimate, app }: { estimate: Estimate; app: string }) {
  const isolation = estimate.isolation;
  const sends = `${count(estimate.tries, "try", "tries")}, ${count(estimate.chats, "message")} to the app`;
  const lines: { text: string; warn?: boolean }[] = [{ text: sends }];
  const at = (seconds: number | null) => (seconds === null ? "" : ` (${about(seconds)})`);
  const each = (seconds: number | null) => (seconds === null ? "" : ` (${about(seconds)} each)`);
  const copy = (start: number | null, restore: number | null) =>
    `Starts evalution's own copy of ${app}${at(start)}, puts it back before each try${each(restore)}, and removes it after. Your app isn't touched.`;
  if (!isolation) {
    if (estimate.isolation_note) lines.push({ text: estimate.isolation_note });
  } else if (isolation.how === "proven") {
    lines.push({ text: copy(isolation.start_seconds, isolation.restore_seconds) });
  } else if (isolation.status === "proven" || isolation.status === "stale") {
    lines.push({ text: `Proves the plan again first${at(isolation.rehearse_seconds)}.` });
    lines.push({ text: copy(isolation.start_seconds, isolation.restore_seconds) });
  } else if (isolation.how === "identity") {
    lines.push({
      text: "Each try is a new person; what the app keeps elsewhere isn't checked.",
    });
  } else {
    lines.push({
      text: "Not isolated: each try may see what earlier tries left behind.",
      warn: true,
    });
  }
  return (
    <div className="flex flex-col items-center gap-1 text-center text-[12.5px]">
      {lines.map((line) => (
        <p key={line.text} className={cn("m-0", line.warn ? "text-warning" : "text-ink-2")}>
          {line.text}
        </p>
      ))}
      {isolation ? <HowItWorks /> : null}
    </div>
  );
}

/** What a run plays: the whole set, one of its tests, or some of them. */
export type RunTarget = {
  scope: Scope;
  test?: { id: string; name: string };
  cases?: readonly string[];
  /** Tries per test, instead of each test's own. */
  tries?: number;
  /** An estimate already in hand, as saving a test with `run` returns one. */
  estimate?: Estimate;
};

/**
 * Every run starts behind this confirm, on what the server estimates. Run uses up the estimate's
 * token, so nothing starts unconfirmed.
 */
export function RunConfirm({
  target,
  onClose,
  onStarted,
}: {
  target: RunTarget;
  onClose: () => void;
  onStarted: (run: RunSummary) => void;
}) {
  const [estimate, setEstimate] = useState<Estimate | null>(target.estimate ?? null);
  const [error, setError] = useState<string | null>(null);
  /** What UI testing lacks, when that's why the run is refused: said with what fixes it. */
  const [missing, setMissing] = useState<MissingPiece | null>(null);
  const [busy, setBusy] = useState(false);

  const given = !!target.estimate;
  const { target: name, set } = target.scope;
  const testId = target.test?.id;
  const cases = target.cases;
  const tries = target.tries;
  useEffect(() => {
    if (given) return;
    let cancelled = false;
    api.estimate({ target: name, set }, testId, cases, tries).then(
      (found) => {
        if (!cancelled) setEstimate(found);
      },
      (failure: unknown) => {
        if (cancelled) return;
        setMissing(missingOf(failure));
        setError(errorText(failure, "Couldn't estimate this run."));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [given, name, set, testId, cases, tries]);

  const start = async () => {
    if (!estimate) return;
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.start(estimate.token));
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409
          ? "Another run is going. Wait for it, or stop it."
          : errorText(failure, "Couldn't start the run."),
      );
      setBusy(false);
    }
  };

  const title = target.test
    ? `Run ${target.test.name}?`
    : cases
      ? `Run ${count(cases.length, "test")}${tries ? ` with ${tries} tries each` : ""}?`
      : estimate
        ? estimate.cases === 1
          ? "Run the 1 test?"
          : `Run all ${estimate.cases} tests?`
        : "Run all tests?";

  return (
    <ConfirmDialog
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      description={estimate || error ? null : "…"}
      footer={
        <>
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!estimate || busy || !estimate.cases} onClick={() => void start()}>
            Run
          </Button>
        </>
      }
    >
      {estimate ? <IsolationLines estimate={estimate} app={name} /> : null}
      {estimate?.refused.map((item) => (
        <p key={item.case} className="m-0 text-center text-[12.5px] text-warning">
          {`Left out: ${item.case}, because ${item.why}`}
        </p>
      ))}
      {missing ? (
        <MissingPieceNote piece={missing} onLeave={onClose} />
      ) : error ? (
        <p className="m-0 text-center text-[12.5px] text-destructive">{error}</p>
      ) : null}
    </ConfirmDialog>
  );
}
