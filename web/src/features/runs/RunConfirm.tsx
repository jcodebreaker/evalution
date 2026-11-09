import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import type { Scope } from "../../lib/api";
import { ApiError, api, errorText, missingOf } from "../../lib/api";
import { cn } from "../../lib/cn";
import { count, runNumbers, tokens } from "../../lib/format";
import { about } from "../../lib/isolation";
import type { Estimate, Keep, MissingPiece, PageReach, Proof, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";
import { useAccount } from "../account/account";
import { useOpenEnvironment } from "../environment/use-isolation";
import { TextLink } from "../shared/group";
import { HowIsolationLink } from "../shared/HowIsolationWorks";
import { PlaysChip } from "../shared/marks";
import { MissingPieceNote } from "../shared/next-step";
import { ProofControl } from "../shared/ProofControl";
import { CodePicker, DISK } from "./CodePicker";
import { Notice } from "./parts";

/** "How this works": the Test environment screen, or the explainer where the page can't move. */
function HowItWorks() {
  const open = useOpenEnvironment();
  return open ? <TextLink onClick={open}>How this works</TextLink> : <HowIsolationLink />;
}

/** What a run sends (unless `sends` is false: the Run dialog says it by its tests), and how its
 * tries are kept apart, in two or three short lines. */
export function IsolationLines({
  estimate,
  app,
  sends = true,
}: {
  estimate: Estimate;
  app: string;
  sends?: boolean;
}) {
  const isolation = estimate.isolation;
  const lines: { text: string; warn?: boolean }[] = sends ? [{ text: sendsWords(estimate) }] : [];
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
  if (!lines.length && !isolation) return null;
  return (
    <div className="flex flex-col items-start gap-1 text-[13px]">
      {lines.map((line) => (
        <p key={line.text} className={cn("m-0", line.warn ? "text-warning" : "text-ink-2")}>
          {line.text}
        </p>
      ))}
      {isolation ? <HowItWorks /> : null}
    </div>
  );
}

/** What a run sends the app: "4 tries, 8 messages to the app". */
function sendsWords(estimate: Estimate): string {
  return `${count(estimate.tries, "try", "tries")}, ${count(estimate.chats, "message")} to the app`;
}

/** A rough time, as the server's estimate says one: "about 35 s", "about 3 min". */
export function rough(seconds: number): string {
  if (seconds < 90) return `about ${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  return `about ${Math.round(seconds / 60)} min`;
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** One way the run's tests play, as its group says it: how many tests through what, then how
 * they play, how long a try takes and what it keeps. */
function playWords(line: Estimate["plays"][number], app: string): { head: string; more: string } {
  const way = line.how === "api" ? "API" : line.how === "page_only" ? "page alone" : "page";
  const head = `${count(line.cases.length, "test")} through ${app}'s ${way}`;
  if (line.how === "api") {
    return { head, more: `${capital(rough(line.seconds))} a try. Nothing to capture.` };
  }
  const kept = line.kept ? ` ${capital(line.kept)}.` : "";
  return { head, more: `One at a time, ${rough(line.seconds)} a try.${kept}` };
}

/** How the run's tests play, what each try keeps and roughly how long one takes: a group for
 * each way they play, badged Page or API ("3 tests through relay-lite's page / One at a
 * time, about 40 s a try. Screenshots of every message, video when a try fails."). */
export function PlaysLines({ estimate, app }: { estimate: Estimate; app: string }) {
  if (!estimate.plays.length) return null;
  return (
    <ul className="m-0 flex list-none flex-col gap-2.5 p-0 text-[13px]" data-testid="run-plays">
      {estimate.plays.map((line) => {
        // An older server's line, without its tests: said as it comes.
        const words = line.cases.length && line.seconds ? playWords(line, app) : null;
        return (
          <li key={line.text} className="flex items-start gap-2.5" data-how={line.how}>
            <span className="pt-px">
              <PlaysChip plays={line.how === "api" ? "api" : "page"} />
            </span>
            {words ? (
              <span className="flex min-w-0 flex-col">
                <span className="font-medium text-foreground">{words.head}</span>
                <span className="text-ink-2">{words.more}</span>
              </span>
            ) : (
              <span className="min-w-0 text-foreground">{line.text}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Roughly how long the whole run takes, when its tests play one at a time (through the page):
 * each group's tries at its seconds a try, with starting the copy and putting it back before each
 * try when those were timed. `null` where tests may play side by side, or nothing says. */
export function runSeconds(estimate: Estimate): number | null {
  const lines = estimate.plays;
  if (!lines.length || !estimate.cases || lines.some((line) => line.how === "api")) return null;
  if (lines.some((line) => !line.cases.length || !line.seconds)) return null;
  const triesEach = estimate.tries / estimate.cases;
  const playing = lines.reduce(
    (sum, line) => sum + line.cases.length * triesEach * line.seconds,
    0,
  );
  const isolation = estimate.isolation;
  const copy =
    isolation?.how === "proven" || isolation?.status === "proven" || isolation?.status === "stale"
      ? (isolation.start_seconds ?? 0) + (isolation.restore_seconds ?? 0) * estimate.tries
      : 0;
  return playing + copy;
}

/**
 * What every try played through the app's page keeps, set right in the Run dialog: the same
 * per-app setting the Test environment card writes. Only where the run plays through the page and
 * the setting can be written here. `onChanged`: after a change, so the dialog says it again.
 */
function RunProof({ app, onChanged }: { app: string; onChanged: () => void }) {
  const hosted = useAccount() !== null;
  const [reach, setReach] = useState<PageReach | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (hosted) return;
    let cancelled = false;
    api.pageReach(app).then(
      (found) => {
        if (!cancelled) setReach(found);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [app, hosted]);
  if (hosted || !reach?.proof || !reach.writable || reach.plays === "api") return null;
  const change = async (part: keyof Proof, keep: Keep) => {
    setWorking(true);
    setError(null);
    try {
      setReach(await api.setPageProof(app, { [part]: keep }));
      onChanged();
    } catch (failure) {
      setError(errorText(failure, "Couldn't change what each try keeps."));
    }
    setWorking(false);
  };
  return (
    <Row label="Keeps">
      <div className="flex flex-col gap-2 text-[13px]" data-testid="run-proof">
        <ProofControl
          proof={reach.proof}
          disabled={working}
          onChange={(part, keep) => void change(part, keep)}
        />
        <p className="m-0 text-[12px] text-ink-3">
          For every page test of {app}. A test can set its own under "What it checks".
        </p>
        {error ? <p className="m-0 text-fail">{error}</p> : null}
      </div>
    </Row>
  );
}

/** A row of the Run dialog: what it is on the start, the run's choice of it beside. */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] items-start gap-4 border-t border-separator px-6 py-3 max-[560px]:grid-cols-1 max-[560px]:gap-1">
      <div className="pt-px text-[13px] text-ink-3">{label}</div>
      <div className="min-w-0 text-[14px]">{children}</div>
    </div>
  );
}

/** Which tests a run plays, and how many tries each: "All 4 tests · 1 try each". */
function testsWords(estimate: Estimate, target: RunTarget): string {
  const which = target.test
    ? target.test.name
    : target.cases
      ? count(estimate.cases, "test")
      : estimate.cases === 1
        ? "The 1 test"
        : `All ${estimate.cases} tests`;
  const each = estimate.cases ? estimate.tries / estimate.cases : 0;
  const tries = Number.isInteger(each)
    ? `${count(each, "try", "tries")} each`
    : `${count(estimate.tries, "try", "tries")} in all`;
  return estimate.cases ? `${which} · ${tries}` : which;
}

/** The run before, in plain words, as far as the estimate and the set's last run have numbers:
 * "About 3 min · starts the copy (about 81 s), then 4 tries · about 103k tokens, as run 8 spent". */
export function estimateWords(estimate: Estimate, spent: Spent | null): string {
  const total = runSeconds(estimate);
  const isolation = estimate.isolation;
  const copied =
    isolation?.how === "proven" || isolation?.status === "proven" || isolation?.status === "stale";
  const tries = count(estimate.tries, "try", "tries");
  const plays =
    copied && isolation?.start_seconds
      ? `starts the copy (${about(isolation.start_seconds)}), then ${tries}`
      : tries;
  return [
    total ? capital(rough(total)) : null,
    total ? plays : capital(plays),
    spent && spent.cases === estimate.cases
      ? `about ${tokens(spent.tokens)} tokens, as run ${spent.n} spent`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** What the set's last full run spent: its number, its tests and its tokens. */
type Spent = { n: number; cases: number; tokens: number };

/** The tokens the set's latest finished run of all its tests spent, to say what this one may. */
function useSpent(scope: Scope, whole: boolean): Spent | null {
  const [spent, setSpent] = useState<Spent | null>(null);
  const { target, set } = scope;
  useEffect(() => {
    setSpent(null);
    if (!whole) return;
    let cancelled = false;
    api.runs({ target, set }).then(
      (runs) => {
        if (cancelled) return;
        const numbers = runNumbers(runs);
        const last = runs
          .filter((run) => run.status === "done" && run.scope === "all" && run.totals.tokens > 0)
          .sort((a, b) => b.started_at - a.started_at)[0];
        const n = last ? numbers.get(last.run) : undefined;
        if (last && n) setSpent({ n, cases: last.totals.cases, tokens: last.totals.tokens });
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [target, set, whole]);
  return spent;
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
  /** The version to play, a branch's name ("main"), as "Run them on main" from a comparison
   * opens it (ADR 0011); what's on disk when absent. */
  ref?: string;
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
  /** Read again after proof changed here, so the dialog says what a try keeps now. */
  const [round, setRound] = useState(0);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** What UI testing lacks, when that's why the run is refused: said with what fixes it. */
  const [missing, setMissing] = useState<MissingPiece | null>(null);
  const [busy, setBusy] = useState(false);
  /** The version it plays: what's on disk now, unless another is picked (ADR 0011). */
  const [ref, setRef] = useState(target.ref ?? DISK);
  const spent = useSpent(target.scope, !target.test && !target.cases && !target.tries);

  const given = !!target.estimate;
  const { target: name, set } = target.scope;
  const testId = target.test?.id;
  const cases = target.cases;
  const tries = target.tries;
  useEffect(() => {
    // An estimate in hand is of what's on disk: another version is estimated on its own.
    if (given && round === 0 && ref === DISK) return;
    let cancelled = false;
    setReading(true);
    setError(null);
    setMissing(null);
    api.estimate({ target: name, set }, testId, cases, tries, ref).then(
      (found) => {
        if (cancelled) return;
        setEstimate(found);
        setReading(false);
      },
      (failure: unknown) => {
        if (cancelled) return;
        // A version that can't run: said, and Run waits for another pick.
        if (ref !== DISK) setEstimate(null);
        setMissing(missingOf(failure));
        setError(errorText(failure, "Couldn't estimate this run."));
        setReading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [given, round, name, set, testId, cases, tries, ref]);

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
    ? `Run ${target.test.name}`
    : cases
      ? `Run ${count(cases.length, "test")}${tries ? ` with ${tries} tries each` : ""}`
      : estimate
        ? estimate.cases === 1
          ? "Run the 1 test"
          : `Run all ${estimate.cases} tests`
        : "Run all tests";

  const run = estimate?.cases ? `Run ${count(estimate.cases, "test")}` : "Run";
  const page = estimate?.plays.some((line) => line.how !== "api") ?? false;
  const plays = estimate ? (
    <>
      <PlaysLines estimate={estimate} app={name} />
      <IsolationLines estimate={estimate} app={name} sends={false} />
    </>
  ) : null;

  return (
    <ConfirmDialog
      wide
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      description={estimate || error ? null : "…"}
      footer={
        <>
          {estimate ? (
            <p
              className="m-0 min-w-0 flex-[1_1_220px] text-[13px] text-ink-2"
              data-testid="run-estimate"
            >
              {estimateWords(estimate, spent)}
            </p>
          ) : null}
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!estimate || busy || reading || !estimate.cases}
            onClick={() => void start()}
          >
            {run}
          </Button>
        </>
      }
    >
      <div className="-mx-6 flex flex-col">
        {estimate ? (
          <Row label="Tests">
            <span className="font-medium">{testsWords(estimate, target)}</span>
            <small className="mt-0.5 block text-[12px] text-ink-3">{sendsWords(estimate)}</small>
          </Row>
        ) : null}
        {estimate?.version || ref !== DISK ? (
          <Row label="Code">
            <CodePicker
              app={name}
              picked={ref}
              version={estimate?.ref === ref ? estimate.version : null}
              onRef={setRef}
            />
          </Row>
        ) : null}
        {plays ? <Row label="How it plays">{plays}</Row> : null}
        {page ? <RunProof app={name} onChanged={() => setRound((n) => n + 1)} /> : null}
      </div>
      {estimate?.refused.length || missing || error ? (
        <div className="flex flex-col gap-2">
          {estimate?.refused.map((item) => (
            <Notice key={item.case} tone="amber">
              <p className="m-0 text-warning">{`Left out: ${item.case}, because ${item.why}`}</p>
            </Notice>
          ))}
          {missing ? (
            <MissingPieceNote piece={missing} onLeave={onClose} />
          ) : error ? (
            <p className="m-0 text-[13px] text-fail">{error}</p>
          ) : null}
        </div>
      ) : null}
    </ConfirmDialog>
  );
}
