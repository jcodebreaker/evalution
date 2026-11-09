import { cn } from "../../lib/cn";
import type { Health } from "../../lib/schema";
import { CARD } from "./card";

const LEVEL = {
  strong: { word: "Strong", bar: "bg-pass" },
  ok: { word: "OK", bar: "bg-unjudged" },
  weak: { word: "Weak", bar: "bg-fail" },
} as const;

/** "What it's good at": each area's share of tests passing in their latest runs. */
export function Areas({ areas }: { areas: Health["areas"] }) {
  return (
    <section className={cn(CARD, "flex flex-col gap-4")} aria-labelledby="areas">
      <div className="flex items-baseline gap-2">
        <h2 id="areas" className="m-0 text-[16px] font-semibold">
          What it's good at
        </h2>
        <span className="ms-auto text-[12px] text-ink-3">share of tests passing</span>
      </div>
      {areas.map((area) => {
        const level = LEVEL[area.level];
        return (
          <div key={area.name} className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-2 text-[14px]">
              <span className="font-medium">{area.name}</span>
              <span className="text-[12px] text-ink-3">{level.word}</span>
              <span className="ms-auto font-mono text-[13px] text-ink-2 tabular-nums">
                {area.pct}%
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-[3px] bg-sunk-2">
              <div
                className={cn("h-full rounded-[3px]", level.bar)}
                style={{ width: `${area.pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </section>
  );
}
