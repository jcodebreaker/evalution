import { useState } from "react";
import type { Scope } from "../../lib/api";
import { api, errorText } from "../../lib/api";
import { asSentence, count } from "../../lib/format";
import type { Health, Isolation } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { TextLink } from "../shared/group";
import { IsolateButton } from "../shared/IsolateButton";
import { PANEL, PANEL_HEAD } from "./card";
import { Following } from "./HowTestsRun";
import { NeedRow } from "./NeedRow";
import { OfferRow, type PageOfferState } from "./OfferRow";
import type { Need, Tone } from "./overview";
import { namesOf } from "./overview";
import { PageMapCard } from "./PageMapCard";

/** What a Needs-you row's actions reach. */
export type NeedsContext = {
  scope: Scope;
  health: Health | null;
  offer: PageOfferState;
  going: boolean;
  onRun: (run: string) => void;
  onRunSome: (set: string, cases: readonly string[]) => void;
  onGoing: () => void;
  onTest: (id: string, run?: string) => void;
  onReview: () => void;
  onWrite: (text: string) => void;
  /** Read the health again: what a row did changed it. */
  onChanged: () => void;
};

/** "Needs you": one ranked list, each row a dot (or New), a bold title, a line, one action. */
export function NeedsYou({ needs, context }: { needs: Need[]; context: NeedsContext }) {
  return (
    <section className={PANEL} aria-labelledby="needs-you" data-testid="needs-you">
      <div className={needs.length ? PANEL_HEAD : `${PANEL_HEAD} border-b-0`}>
        <h2 id="needs-you" className="m-0 text-[15px] font-semibold">
          Needs you
        </h2>
        {needs.length ? (
          <span className="inline-flex h-7 items-center rounded-full border border-rule px-2.5 font-mono text-[13px] text-ink-2 tabular-nums">
            {needs.length}
          </span>
        ) : null}
      </div>
      {needs.length ? (
        <ul className="m-0 list-none p-0">
          {needs.map((need) => (
            <NeedItem key={need.id} need={need} context={context} />
          ))}
        </ul>
      ) : (
        <p className="m-0 px-5 pt-1 pb-4 text-sm text-ink-3" data-testid="needs-you-empty">
          Nothing needs you right now.
        </p>
      )}
    </section>
  );
}

function NeedItem({ need, context }: { need: Need; context: NeedsContext }) {
  const { scope, health } = context;
  switch (need.kind) {
    case "worse":
      return (
        <NeedRow
          tone={need.tone}
          testId="need-worse"
          title={`${count(need.count, "test")} got worse in run ${need.n}`}
          line={
            need.names.length
              ? `${namesOf(need.names)} passed in run ${need.baseN}, the run it's compared with.`
              : `Compared with run ${need.baseN}.`
          }
          action={
            <Button variant="secondary" size="sm" onClick={() => context.onRun(need.run)}>
              Open run {need.n}
            </Button>
          }
        />
      );
    case "stopped":
      return (
        <NeedRow
          tone={need.tone}
          testId="need-stopped"
          title={
            need.interrupted
              ? `Run ${need.n} was interrupted`
              : need.started
                ? `Run ${need.n} stopped before it finished`
                : `Run ${need.n} didn't start`
          }
          line={
            need.interrupted
              ? "The server restarted during it, which says nothing of the app."
              : need.why
                ? asSentence(need.why)
                : null
          }
          action={
            <Button variant="secondary" size="sm" onClick={() => context.onRun(need.run)}>
              Open run {need.n}
            </Button>
          }
        />
      );
    case "page":
      return <PageMapCard target={scope.target} page={health?.page ?? null} />;
    case "version":
      return (
        <NeedRow
          tone={need.tone}
          testId="need-version"
          title="Runs may not have played this commit"
          line={need.why ? asSentence(need.why) : null}
          action={
            <Button variant="link" size="sm" onClick={() => context.onRun(need.run)}>
              See what played →
            </Button>
          }
        />
      );
    case "isolation":
      return health?.isolation ? (
        <IsolationRow target={scope.target} card={health.isolation} />
      ) : null;
    case "change":
      return health?.code_change ? (
        <ChangeRow change={health.code_change} context={context} />
      ) : null;
    case "knowledge":
      return health ? <KnowledgeRow need={need} health={health} context={context} /> : null;
    case "attention":
      return <AttentionRow item={need.item} tone={need.tone} context={context} />;
    case "offer":
      return <OfferRow target={scope.target} state={context.offer} />;
  }
}

const ACTION: Record<Health["attention"][number]["action"], string> = {
  see_why: "See why",
  open_test: "Open test",
  review: "Review",
};

function AttentionRow({
  item,
  tone,
  context,
}: {
  item: Health["attention"][number];
  tone: Tone;
  context: NeedsContext;
}) {
  const act = () => {
    if (item.action === "review") context.onReview();
    else if (item.case)
      context.onTest(item.case, item.action === "see_why" ? (item.run ?? undefined) : undefined);
    else if (item.run) context.onRun(item.run);
  };
  return (
    <NeedRow
      tone={tone}
      testId="need-attention"
      title={item.title}
      line={item.detail || null}
      action={
        <Button variant="secondary" size="sm" onClick={act}>
          {ACTION[item.action]}
        </Button>
      }
    />
  );
}

/** An isolation plan a commit left out of date, or a rehearsal found leaking: the button that
 * proves it again, then its steps as they run. */
function IsolationRow({
  target,
  card,
}: {
  target: string;
  card: NonNullable<Health["isolation"]>;
}) {
  const [started, setStarted] = useState<Isolation | null>(null);
  const stale = card.status === "stale";
  return (
    <NeedRow
      tone="unjudged"
      testId="need-isolation"
      title={
        stale ? "The plan that starts each try clean is out of date" : "Tries aren't isolated yet"
      }
      line={
        stale
          ? `A commit changed ${card.stale_files.join(", ") || "a file it rests on"}. The next run proves it again first, on evalution's copy of ${target}.`
          : `The rehearsal found ${target} keeping things the plan doesn't put back${card.leaks.length ? `: ${card.leaks.join(", ")}` : ""}.`
      }
      action={
        started ? null : (
          <IsolateButton
            target={target}
            label={stale ? "Prove it again now" : "Find and prove it again"}
            disabled={!card.can_run}
            onStarted={setStarted}
          />
        )
      }
    >
      {started ? <Following target={target} /> : null}
    </NeedRow>
  );
}

/** What changed in the code since it was last tested: run the tests it touches. */
function ChangeRow({
  change,
  context,
}: {
  change: NonNullable<Health["code_change"]>;
  context: NeedsContext;
}) {
  const { scope, going } = context;
  const { areas, tests, uncovered, summary, file_count: files } = change;
  const [reading, setReading] = useState(false);
  const canRead = context.health?.knowledge.can_update ?? false;
  const read = () => {
    setReading(true);
    api
      .readChange(scope.target)
      .then(context.onChanged, () => undefined)
      .finally(() => setReading(false));
  };
  // One run is one test set: this one first, the rest once it has run.
  const set = tests.some((test) => test.set === scope.set) ? scope.set : tests[0]?.set;
  const cases = tests.filter((test) => test.set === set).map((test) => test.case);
  const line = (
    <>
      {tests.length ? `${count(tests.length, "test")} affected` : null}
      {tests.length && uncovered.length ? " · " : null}
      {uncovered.length ? (
        <>
          {uncovered.length === 1
            ? "1 area has no test: "
            : `${uncovered.length} areas have no test: `}
          {uncovered.map((area, index) => (
            <span key={area}>
              {index ? ", " : null}
              <TextLink onClick={() => context.onWrite(writeAsk(area, summary))}>{area}</TextLink>
            </span>
          ))}
        </>
      ) : null}
      {!change.read && canRead && set && cases.length ? (
        <>
          {" · "}
          <TextLink disabled={reading} onClick={read}>
            {reading ? "Reading it…" : "Read this change"}
          </TextLink>
        </>
      ) : null}
    </>
  );
  const action =
    set && cases.length ? (
      going ? (
        <Button variant="secondary" size="sm" onClick={context.onGoing}>
          <Spinner />
          Running
        </Button>
      ) : (
        <Button variant="secondary" size="sm" onClick={() => context.onRunSome(set, cases)}>
          {cases.length === tests.length ? "Run them" : `Run ${count(cases.length, "test")}`}
        </Button>
      )
    ) : !change.read && canRead ? (
      <Button variant="secondary" size="sm" disabled={reading} onClick={read}>
        {reading ? <Spinner /> : null}
        Read this change
      </Button>
    ) : null;
  return (
    <NeedRow
      tone="unjudged"
      testId="need-change"
      title={
        <span title={summary || undefined}>
          Code changed since the last run: {areas.length ? areas.join(", ") : count(files, "file")}
        </span>
      }
      line={line}
      action={action}
    />
  );
}

/** The ask an area with no test puts in the test writer's reply field. */
export function writeAsk(area: string, summary: string): string {
  return `Propose a test for ${area}.${summary ? ` What changed: ${summary}` : ""}`;
}

/** What it knows may be out of date (Update), or corrections a commit may have undone (Review). */
function KnowledgeRow({
  need,
  health,
  context,
}: {
  need: Extract<Need, { kind: "knowledge" }>;
  health: Health;
  context: NeedsContext;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { knowledge } = health;
  const last = knowledge.last;
  const update = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.updateKnowledge(context.scope.target);
      context.onChanged();
    } catch (failure) {
      setError(errorText(failure, "Couldn't update. Nothing changed."));
    }
    setBusy(false);
  };
  const lastLine = last
    ? last.changed
      ? `It updated what it knows from ${last.commit}: ${count(last.changed, "claim")} changed.`
      : `It checked what it knows against ${last.commit}.`
    : null;
  if (need.out) {
    return (
      <NeedRow
        tone="unjudged"
        testId="need-knowledge"
        title={`${count(need.out, "thing")} it knows may be out of date`}
        line={
          <>
            {lastLine}
            {need.flagged ? (
              <>
                {lastLine ? " " : null}
                <TextLink onClick={context.onReview}>
                  {need.flagged} of your corrections may be out of date
                </TextLink>
              </>
            ) : null}
            {error ? <span className="block text-destructive">{error}</span> : null}
          </>
        }
        action={
          knowledge.can_update ? (
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => void update()}>
              {busy ? <Spinner /> : null}
              Update
            </Button>
          ) : (
            <Button variant="secondary" size="sm" onClick={context.onReview}>
              Review
            </Button>
          )
        }
      />
    );
  }
  return (
    <NeedRow
      tone="unjudged"
      testId="need-knowledge"
      title={`${need.flagged} of your corrections may be out of date`}
      line={lastLine}
      action={
        <Button variant="secondary" size="sm" onClick={context.onReview}>
          Review
        </Button>
      }
    />
  );
}
