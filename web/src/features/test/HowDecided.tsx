import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { count } from "../../lib/format";
import type { CheckRow, Explain, TryOutcome } from "../../lib/schema";
import { checkWord } from "../../lib/verdict";
import { SectionHead } from "./SectionHead";

const MARK: Record<TryOutcome, [string, string]> = {
  passed: ["✓", "text-success"],
  failed: ["✕", "text-destructive"],
  unjudged: ["?", "text-ink-2"],
};

function Row({ check }: { check: CheckRow }) {
  const [glyph, tone] = MARK[check.outcome];
  return (
    <li className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-baseline gap-1.5 text-[12.5px]">
      <span
        role="img"
        aria-label={checkWord({
          passed: check.outcome === "passed",
          unjudged: check.outcome === "unjudged",
        })}
        className={cn("font-bold", tone)}
      >
        {glyph}
      </span>
      <span className="break-words text-ink-2">{check.text}</span>
      {check.guide ? <span className="text-[11px] text-ink-3">guide</span> : null}
    </li>
  );
}

function Panel({
  title,
  tally,
  children,
}: {
  title: string;
  tally: string | null;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2 border-separator p-3.5 not-first:md:border-s not-first:max-md:border-t">
      <h4 className="m-0 text-[11.5px] font-semibold text-ink-3 uppercase">{title}</h4>
      {tally ? <b className="text-[15px] font-semibold">{tally}</b> : null}
      {children}
    </div>
  );
}

/** What decided the try: what code checked, apart from what a model graded, and on what footing. */
export function HowDecided({ explain }: { explain: Explain }) {
  const { code, model } = explain;
  const graded = model.total > 0 || !!model.reference;
  if (!code.checks.length && !graded) return null;
  const footing = [
    explain.judge ? `graded by ${explain.judge}` : null,
    `rules ${explain.rules}`,
    explain.rules_from === null ? null : `first judged under rules ${explain.rules_from}`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <section className="flex flex-col gap-2">
      <SectionHead title="How it was decided" sub={footing} />
      <div className={cn("group-card grid", code.checks.length && graded && "md:grid-cols-2")}>
        {code.checks.length ? (
          <Panel
            title="Decided by code"
            tally={code.total ? `${code.held} of ${count(code.total, "check")} held` : null}
          >
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {code.checks.map((check) => (
                <Row key={check.id} check={check} />
              ))}
            </ul>
          </Panel>
        ) : null}
        {graded ? (
          <Panel
            title="Graded by a model"
            tally={
              model.total
                ? (model.summary ?? `${model.held} of ${count(model.total, "line")} held`)
                : null
            }
          >
            {model.reference ? (
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                <Row check={model.reference} />
              </ul>
            ) : null}
            {model.unjudged ? (
              <p className="m-0 text-[12.5px] text-ink-2">{`${model.unjudged} couldn't be judged`}</p>
            ) : null}
            {model.cut ? (
              <p className="m-0 text-[12.5px] text-ink-2">
                {`It wasn't shown ${model.cut.toLocaleString()} characters of the evidence`}
              </p>
            ) : null}
          </Panel>
        ) : null}
      </div>
    </section>
  );
}
