import { CircleAlert, ShieldCheck } from "lucide-react";
import { type ReactNode, useState } from "react";
import { cn } from "../../lib/cn";
import { storeLabel } from "../../lib/isolation";
import type { Health, Isolation } from "../../lib/schema";
import { Checklist } from "../environment/Checklist";
import { useIsolation, useOpenEnvironment } from "../environment/use-isolation";
import { TextLink } from "../shared/group";
import { HowIsolationLink } from "../shared/HowIsolationWorks";
import { ISOLATE_COST, IsolateButton } from "../shared/IsolateButton";
import { CARD } from "./card";

type Card = NonNullable<Health["isolation"]>;

/** The day a plan was proven, as a person says it. */
const day = (at: number) =>
  new Date(at * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

/**
 * Home's "How tests run": whether each try starts clean, and why a result can be trusted, for
 * every state of the isolation plan. Proven: what is put back before every try and what is left
 * alone. Out of date or failed: what changed or leaked, and the button. None: what tries may see
 * of earlier ones, what the connection itself promises, and the button.
 */
export function HowTestsRun({
  target,
  isolation,
}: {
  target: string;
  isolation: Health["isolation"];
}) {
  const [started, setStarted] = useState(false);
  const open = useOpenEnvironment();
  if (!isolation) return null;
  const proven = isolation.status === "proven";
  const links = (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-ink-3">
      {proven ? (
        <span>
          Proven
          {isolation.updated ? ` ${day(isolation.updated)}` : ""}
          {isolation.version !== null ? `, plan v${isolation.version}` : ""}
        </span>
      ) : null}
      <HowIsolationLink />
      {open ? <TextLink onClick={open}>Details</TextLink> : null}
    </div>
  );
  return (
    <section
      className={cn(CARD, "flex flex-col gap-3 text-[13px]")}
      aria-label="How tests run"
      data-testid="how-tests-run"
    >
      <h2 className="m-0 flex items-center gap-2 text-[15px] font-semibold">
        {proven ? (
          <ShieldCheck className="size-4 text-success" aria-hidden />
        ) : (
          <CircleAlert className="size-4 text-warning" aria-hidden />
        )}
        How tests run
      </h2>
      {started ? (
        <Following target={target} />
      ) : (
        <Body target={target} card={isolation} onStarted={() => setStarted(true)} />
      )}
      {links}
    </section>
  );
}

function Body({ target, card, onStarted }: { target: string; card: Card; onStarted: () => void }) {
  const reset = card.stores.filter((store) => store.reset);
  const kept = card.stores.filter((store) => !store.reset);
  const button = (label?: string) => (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2.5">
        <IsolateButton
          target={target}
          label={label}
          disabled={!card.can_run}
          onStarted={onStarted}
        />
      </div>
      <p className="m-0 text-[12px] text-ink-3">
        {card.can_run ? ISOLATE_COST : "Say where the app's code is first: it's found from it."}
      </p>
    </div>
  );
  const problem = card.problem ? <p className="m-0 text-destructive">{card.problem}</p> : null;

  switch (card.status) {
    case "proven":
      return (
        <>
          <p className="m-0 max-w-[68ch]">
            Each try starts clean. evalution runs its own copy of {target} and puts it back before
            every try, so a pass never rests on what an earlier try left behind.
          </p>
          {reset.length ? (
            <Listed title="Put back before every try" items={reset.map(storeLabel)} />
          ) : null}
          {kept.length ? (
            <Listed
              title="Not put back: each try uses its own person here"
              items={kept.map(storeLabel)}
            />
          ) : null}
          {card.exclude.length ? (
            <Listed title="Left alone, as a record of what happened" items={card.exclude} />
          ) : null}
        </>
      );
    case "stale":
      return (
        <>
          <p className="m-0 max-w-[68ch]">
            The plan that starts each try clean is out of date: a commit changed{" "}
            {card.stale_files.join(", ") || "a file it rests on"}. The next run proves it again
            first, on evalution's own copy of {target}.
          </p>
          {button("Prove it again now")}
        </>
      );
    case "failed":
      return (
        <>
          <p className="m-0 max-w-[68ch] text-warning">
            Tries aren't isolated yet: the rehearsal found {target} keeping things the plan doesn't
            put back, so tries may see what earlier ones left.
          </p>
          {card.leaks.length ? <Listed title="Where it leaked" items={card.leaks} mono /> : null}
          {button("Find and prove it again")}
        </>
      );
    default:
      return (
        <>
          <p className="m-0 max-w-[68ch] text-warning">
            Tries aren't isolated. Each try can see what earlier tries left behind: a pass may rest
            on leftovers, and a fail may be caused by one.
          </p>
          {card.note ? (
            <p className="m-0 max-w-[68ch] text-ink-2">
              On its own, the connection promises: {card.note}
            </p>
          ) : null}
          {problem}
          {button()}
        </>
      );
  }
}

function Listed({ title, items, mono }: { title: string; items: string[]; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12.5px] font-medium text-ink-2">{title}</span>
      <ul className="m-0 flex flex-col gap-0.5 ps-4">
        {items.map((item) => (
          <li key={item} className={cn(mono && "font-mono text-[12px] break-all")}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** After the button: the rehearsal's steps as they run. */
function Following({ target }: { target: string }): ReactNode {
  const { data } = useIsolation(target);
  const job: Isolation["job"] = data?.job ?? null;
  if (!job) return <p className="m-0 text-ink-2">Starting…</p>;
  return (
    <div className="flex flex-col gap-2">
      <Checklist steps={job.steps} />
      {job.error ? <p className="m-0 text-[12.5px] text-destructive">{job.error}</p> : null}
    </div>
  );
}
