import { useMemo } from "react";
import { cn } from "../../lib/cn";
import { seconds, spanDepths, timelineAxis } from "../../lib/format";
import type { Span } from "../../lib/schema";

const BAR: Record<Span["kind"], string> = {
  agent: "top-[11px] h-1 bg-foreground/80",
  tool: "top-2 h-2.5 bg-ink-3/60",
  model: "top-2 h-2.5 bg-ink-2",
  missing: "top-[7px] h-3 border-[1.5px] border-dashed border-destructive",
};

const ROW =
  "grid grid-cols-[minmax(0,200px)_minmax(0,1fr)_52px] items-center px-3 max-md:grid-cols-[110px_minmax(0,1fr)_44px]";

function Tag({ children }: { children: string }) {
  return <span className="shrink-0 text-[10.5px] text-ink-3">{children}</span>;
}

/** A try's spans on one time axis, indented by parent. */
export function Timeline({
  spans,
  selected,
  onSelect,
}: {
  spans: Span[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const depths = useMemo(() => spanDepths(spans), [spans]);
  const axis = useMemo(() => timelineAxis(spans), [spans]);
  return (
    <div>
      <div className="flex justify-end gap-3 px-1 pb-1.5 text-[12.5px] text-ink-3">
        {spans.some((span) => span.automatic) ? (
          <span className="me-auto">Automatic: ran by itself, not scored</span>
        ) : null}
        <span className="inline-flex items-center gap-1.5">
          <i className="h-1.5 w-2.5 rounded-[2px] bg-ink-3/60" />
          Tool
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="h-1.5 w-2.5 rounded-[2px] bg-ink-2" />
          Model
        </span>
      </div>
      <div className="rounded-lg border border-separator bg-group pt-1.5 pb-2">
        <div className={cn(ROW, "py-1 text-[11px] text-ink-3")}>
          <span />
          <div className="flex justify-between tabular-nums">
            {axis.ticks.map((tick) => (
              <span key={tick}>{tick === 0 ? "0" : seconds(tick)}</span>
            ))}
          </div>
          <span />
        </div>
        {spans.map((span) => {
          const missing = span.kind === "missing";
          const red = missing || !!span.check;
          const cells = (
            <>
              <span
                className={cn(
                  "flex min-w-0 items-center gap-1.5 whitespace-nowrap text-[13px]",
                  red && "text-destructive",
                )}
                style={{ paddingInlineStart: (depths.get(span.id) ?? 0) * 14 }}
              >
                <span className="truncate">{missing ? `Expected: ${span.name}` : span.name}</span>
                {span.helper ? <Tag>helper</Tag> : null}
                {span.automatic ? <Tag>automatic</Tag> : null}
              </span>
              <span className="relative h-full">
                {[25, 50, 75].map((line) => (
                  <i
                    key={line}
                    className="absolute inset-y-0 w-px bg-separator"
                    style={{ left: `${line}%` }}
                  />
                ))}
                <span
                  className={cn("absolute rounded-[3px]", BAR[span.kind])}
                  style={{
                    left: `${(span.start / axis.max) * 100}%`,
                    width: `${Math.max((span.seconds / axis.max) * 100, 0.8)}%`,
                  }}
                />
              </span>
              <span className="text-end text-[11.5px] text-ink-3 tabular-nums">
                {missing ? "" : seconds(span.seconds, true)}
              </span>
            </>
          );
          if (missing) {
            return (
              <div key={span.id} className={cn(ROW, "h-[26px]")}>
                {cells}
              </div>
            );
          }
          return (
            <button
              key={span.id}
              type="button"
              aria-pressed={span.id === selected}
              onClick={() => onSelect(span.id)}
              className={cn(
                ROW,
                "h-[26px] w-full text-start focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                span.id === selected ? "bg-selection-strong" : "hover:bg-selection",
              )}
            >
              {cells}
            </button>
          );
        })}
      </div>
    </div>
  );
}
