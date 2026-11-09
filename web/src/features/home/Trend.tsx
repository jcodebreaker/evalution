import { cn } from "../../lib/cn";
import { when } from "../../lib/format";
import type { Health } from "../../lib/schema";
import { CARD } from "./card";

const LEFT = 50;

const RIGHT = 500;

const TOP = 20;

const BOTTOM = 180;

/** Tests passing per run, oldest on the left, with where the app changed. */
export function Trend({ trend }: { trend: Health["trend"] }) {
  const step = (RIGHT - LEFT) / Math.max(1, trend.length - 1);
  const points = trend.map((entry, index) => ({
    entry,
    x: LEFT + index * step,
    y: BOTTOM - (entry.total ? entry.passing / entry.total : 0) * (BOTTOM - TOP),
  }));
  const line = points.map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" ");
  const first = trend[0];
  const last = trend.at(-1);
  const day = (at: number) =>
    new Date(at * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  const isToday = (at: number) => new Date(at * 1000).toDateString() === new Date().toDateString();
  return (
    <div className={cn(CARD, "flex flex-col gap-2.5")}>
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 text-[17px] font-bold">Over the last {trend.length} runs</h2>
        <span className="ms-auto text-[12.5px] text-ink-2">tests passing</span>
      </div>
      <svg
        role="img"
        aria-label={
          first && last
            ? `Tests passing went from ${first.passing} of ${first.total} to ${last.passing} of ${last.total}`
            : "Tests passing"
        }
        viewBox="0 0 520 200"
        className="h-auto w-full overflow-visible"
      >
        <g className="stroke-bubble" strokeWidth="1">
          {[TOP, (TOP + BOTTOM) / 2, BOTTOM].map((y) => (
            <line key={y} x1="36" y1={y} x2="510" y2={y} />
          ))}
        </g>
        <g className="fill-ink-2 text-[11px]">
          <text x="28" y={TOP + 4} textAnchor="end">
            100%
          </text>
          <text x="28" y={(TOP + BOTTOM) / 2 + 4} textAnchor="end">
            50%
          </text>
          <text x="28" y={BOTTOM + 4} textAnchor="end">
            0%
          </text>
        </g>
        <path
          d={`${line} L${RIGHT} ${BOTTOM} L${LEFT} ${BOTTOM} Z`}
          className="fill-tint"
          opacity="0.08"
        />
        <path
          d={line}
          fill="none"
          strokeWidth="2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          className="stroke-tint"
        />
        {points.map((point) =>
          point.entry.marker ? (
            <g key={`m-${point.entry.run}`}>
              <line
                x1={point.x}
                y1={TOP}
                x2={point.x}
                y2={BOTTOM}
                strokeDasharray="3 4"
                strokeWidth="1.2"
                className="stroke-warning"
              />
              <text
                x={point.x > 380 ? point.x - 6 : point.x + 6}
                y={TOP + 14}
                textAnchor={point.x > 380 ? "end" : "start"}
                className="fill-warning text-[11px] font-semibold"
              >
                {point.entry.marker}
              </text>
            </g>
          ) : null,
        )}
        {points.map((point, index) => (
          <circle
            key={point.entry.run}
            cx={point.x}
            cy={point.y}
            r={index === points.length - 1 ? 6 : 4}
            strokeWidth="2"
            className={
              index === points.length - 1 ? "fill-tint stroke-group" : "fill-group stroke-tint"
            }
          >
            <title>{`${point.entry.passing} of ${point.entry.total} · ${when(point.entry.at)}`}</title>
          </circle>
        ))}
        {first && last ? (
          <g className="fill-ink-2 text-[11px]">
            <text x={LEFT} y="196" textAnchor="middle">
              {day(first.at)}
            </text>
            <text x={RIGHT} y="196" textAnchor="end">
              {isToday(last.at) ? "Today" : day(last.at)}
            </text>
          </g>
        ) : null}
      </svg>
    </div>
  );
}
