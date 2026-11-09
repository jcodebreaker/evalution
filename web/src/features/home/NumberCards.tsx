import { cn } from "../../lib/cn";
import { seconds, tokens } from "../../lib/format";
import { CARD } from "./card";
import type { Bar, Passing, Spent, Typical } from "./overview";

// The three numbers under Needs you, each with a small chart of the same number run by run. The
// charts are inline SVG on the page's own colours, so they follow dark mode with the rest.

const W = 180;
const H = 44;
/** Each run's slot; a bar is a little narrower than its slot. */
const slot = (count: number) => W / Math.max(count, 1);

function Shell({
  label,
  value,
  beside,
  sub,
  chart,
  testId,
}: {
  label: string;
  value: string;
  beside?: React.ReactNode;
  sub?: string | null;
  chart: React.ReactNode;
  testId: string;
}) {
  return (
    <section className={cn(CARD, "flex min-w-0 flex-col px-5 py-4")} data-testid={testId}>
      <h2 className="m-0 text-[13px] font-normal text-ink-2">{label}</h2>
      <div className="mt-1 flex flex-wrap items-baseline gap-2">
        <span className="text-[28px] leading-[1.1] font-semibold tracking-[-0.03em] tabular-nums">
          {value}
        </span>
        {beside}
      </div>
      {sub ? <p className="m-0 mt-0.5 text-[12px] text-ink-3">{sub}</p> : null}
      <div className="mt-3">{chart}</div>
    </section>
  );
}

function Delta({ tone, children }: { tone: "ok" | "bad" | "same"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-px text-[12px] font-medium",
        tone === "ok" && "bg-pass-soft text-pass",
        tone === "bad" && "bg-fail-soft text-fail",
        tone === "same" && "bg-sunk text-ink-2",
      )}
    >
      {children}
    </span>
  );
}

const BAR_FILL: Record<Bar["tone"], string> = {
  pass: "fill-pass",
  fail: "fill-fail",
  unjudged: "fill-unjudged",
  none: "fill-sunk-2",
  live: "fill-tint",
};

/** "Passing in the last run": n of N, its move against its base, and each run's share. */
export function PassingCard({ card }: { card: Passing }) {
  const width = slot(card.bars.length);
  const by = card.delta?.by ?? 0;
  return (
    <Shell
      testId="kpi-passing"
      label="Passing in the last run"
      value={`${card.passed} of ${card.total}`}
      beside={
        card.delta ? (
          <Delta tone={by > 0 ? "ok" : by < 0 ? "bad" : "same"}>
            {by === 0
              ? `same as run ${card.delta.n}`
              : `${by > 0 ? "+" : "−"}${Math.abs(by)} vs run ${card.delta.n}`}
          </Delta>
        ) : null
      }
      sub={`Run ${card.n}'s tests passing`}
      chart={
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="block h-11 w-full overflow-visible"
          role="img"
          aria-label={`Tests passing in the last ${card.bars.length} runs`}
        >
          {card.bars.map((bar, at) => {
            const height = Math.max(2, bar.value * (H - 4));
            return (
              <rect
                key={bar.run}
                x={at * width + 2}
                y={H - height}
                width={width - 4}
                height={height}
                rx={height > 4 ? 2 : 1}
                className={BAR_FILL[bar.tone]}
                opacity={bar.latest || bar.tone === "live" ? 1 : 0.6}
                data-tone={bar.tone}
              >
                <title>{`Run ${bar.n}: ${Math.round(bar.value * 100)}% passing`}</title>
              </rect>
            );
          })}
        </svg>
      }
    />
  );
}

/** "Typical try": the middle try's seconds, and a line of it over the recent runs. */
export function TypicalCard({ card }: { card: Typical }) {
  const width = slot(card.points.length);
  const max = Math.max(...card.points.map((point) => point?.value ?? 0), 1);
  const y = (value: number) => H - 2 - (value / max) * (H - 6);
  const shown = card.points.flatMap((point, at) =>
    point ? [{ ...point, x: at * width + width / 2, y: y(point.value) }] : [],
  );
  const last = shown.at(-1);
  const moved = card.before ? Math.round(card.last.value) - Math.round(card.before.value) : null;
  return (
    <Shell
      testId="kpi-typical"
      label="Typical try"
      value={seconds(card.last.value)}
      beside={<span className="text-[13px] text-ink-3">run {card.last.n}</span>}
      sub={
        card.none
          ? `Run ${card.none.n} played no try`
          : card.before && moved !== null
            ? moved === 0
              ? `The same as run ${card.before.n}`
              : `${Math.abs(moved)} s ${moved < 0 ? "faster" : "slower"} than run ${card.before.n}`
            : "Its middle try"
      }
      chart={
        <div className="relative">
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="block h-11 w-full overflow-visible"
            role="img"
            aria-label="The middle try's seconds, run by run"
          >
            {shown.length > 1 ? (
              <polyline
                fill="none"
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
                className="stroke-tint"
                points={shown.map((point) => `${point.x},${point.y}`).join(" ")}
              />
            ) : null}
            {shown.map((point) => (
              <rect key={point.run} x={point.x - 6} y={0} width={12} height={H} fill="transparent">
                <title>{`Run ${point.n}: ${seconds(point.value)}`}</title>
              </rect>
            ))}
          </svg>
          {/* The newest point, round whatever the chart's width: the SVG stretches. */}
          {last ? (
            <span
              aria-hidden
              className="pointer-events-none absolute size-[11px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-group bg-tint"
              style={{ left: `${(last.x / W) * 100}%`, top: `${(last.y / H) * 100}%` }}
            />
          ) : null}
        </div>
      }
    />
  );
}

/** "Spent": the tokens the last run spent, and each recent run's. */
export function SpentCard({ card }: { card: Spent }) {
  const width = slot(card.bars.length);
  const max = Math.max(...card.bars.map((bar) => bar.value), 1);
  return (
    <Shell
      testId="kpi-spent"
      label="Spent"
      value={tokens(card.tokens)}
      beside={<span className="text-[13px] text-ink-3">tokens in run {card.n}</span>}
      sub={
        card.tokens === 0
          ? `Run ${card.n} spent nothing`
          : card.typical !== null
            ? `Typically ${tokens(card.typical)} a run`
            : null
      }
      chart={
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="block h-11 w-full overflow-visible"
          role="img"
          aria-label={`Tokens spent in the last ${card.bars.length} runs`}
        >
          {card.bars.map((bar, at) => {
            const height = bar.value ? Math.max(2, (bar.value / max) * (H - 4)) : 1;
            return (
              <rect
                key={bar.run}
                x={at * width + 2}
                y={H - height}
                width={width - 4}
                height={height}
                rx={bar.value ? 2 : 0}
                className={
                  bar.value ? (bar.latest ? "fill-tint" : "fill-tint-soft-2") : "fill-sunk-2"
                }
              >
                <title>{`Run ${bar.n}: ${bar.value ? `${tokens(bar.value)} tokens` : "nothing"}`}</title>
              </rect>
            );
          })}
        </svg>
      }
    />
  );
}
