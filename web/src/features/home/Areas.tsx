import { cn } from "../../lib/cn";
import type { Health } from "../../lib/schema";
import { CARD } from "./card";

const LEVEL = {
  strong: {
    word: "Strong",
    tag: "bg-selection-strong text-tint",
    bar: "bg-success",
  },
  ok: { word: "OK", tag: "bg-bubble text-ink-2", bar: "bg-mid" },
  weak: { word: "Weak", tag: "bg-warning/12 text-warning", bar: "bg-warning" },
} as const;

export function Areas({ areas }: { areas: Health["areas"] }) {
  return (
    <div className={cn(CARD, "flex flex-col gap-4")}>
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 text-[17px] font-bold">What it's good at</h2>
        <span className="ms-auto text-[12.5px] text-ink-2">share of tests passing</span>
      </div>
      {areas.map((area) => {
        const level = LEVEL[area.level];
        return (
          <div key={area.name} className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-2">
              <span className="text-[14px] font-semibold">{area.name}</span>
              <span
                className={cn("rounded-full px-[7px] py-px text-[12.5px] font-bold", level.tag)}
              >
                {level.word}
              </span>
              <span className="ms-auto font-semibold text-ink-2 tabular-nums">{area.pct}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-[4px] bg-bubble">
              <div
                className={cn("h-full rounded-[4px]", level.bar)}
                style={{ width: `${area.pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
