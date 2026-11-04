import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "../../lib/cn";

/** A chart's card: what it plots and in what unit. */
export function ChartPanel({
  icon: Icon,
  title,
  unit,
  className,
  children,
}: {
  icon: LucideIcon;
  className?: string;
  title: string;
  unit: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("glass flex min-w-0 flex-col gap-3 rounded-xl p-4", className)}>
      <div className="flex flex-wrap items-center justify-between gap-x-3">
        <h3 className="m-0 flex items-center gap-2 text-[13px] font-medium">
          <span className="grid size-6 place-items-center rounded-md border border-separator">
            <Icon className="size-3.5 text-ink-2" aria-hidden />
          </span>
          {title}
        </h3>
        <span className="text-[12px] text-ink-3">{unit}</span>
      </div>
      {children}
    </section>
  );
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const box = ref.current;
    if (!box) return;
    setWidth(box.clientWidth);
    const watch = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    watch.observe(box);
    return () => watch.disconnect();
  }, []);
  return [ref, width] as const;
}

/** The readout over a mark: the value leads, what it is follows. */
function Tip({ left, top, children }: { left: number | string; top: number; children: ReactNode }) {
  return (
    <div
      style={{ left, top }}
      className="pointer-events-none absolute z-10 max-w-[240px] -translate-x-1/2 -translate-y-full rounded-lg bg-popover px-2.5 py-1.5 text-[12px] whitespace-normal shadow-lg ring-1 ring-separator"
    >
      {children}
    </div>
  );
}

const TIP_ROOM = 90;
const tipLeft = (x: number, width: number) =>
  Math.max(Math.min(TIP_ROOM, width / 2), Math.min(width - Math.min(TIP_ROOM, width / 2), x));

const HEIGHT = 190;
const AXIS_LEFT = 36;
const TOP = 18;
const BASE = HEIGHT - 22;

/** A hairline across the plot and its value on the axis. */
function GridLine({ y, width, label }: { y: number; width: number; label: string }) {
  return (
    <>
      <line
        x1={AXIS_LEFT}
        x2={width}
        y1={y}
        y2={y}
        stroke="currentColor"
        className="text-separator"
      />
      <text
        x={AXIS_LEFT - 8}
        y={y + 4}
        textAnchor="end"
        className="fill-ink-3 text-[11px] tabular-nums"
      >
        {label}
      </text>
    </>
  );
}

export type TrendPoint = { id: string; at: string; value: number; label: string; detail: string };

/** One share across runs, oldest first, on a fixed 0 to 100% axis. A point opens its run. */
export function LineChart({
  points,
  name,
  onPick,
}: {
  points: TrendPoint[];
  name: string;
  onPick: (id: string) => void;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const right = width - 46;
  const step = (right - AXIS_LEFT - 8) / Math.max(1, points.length - 1);
  const x = (index: number) => AXIS_LEFT + 8 + index * step;
  const y = (value: number) => BASE - value * (BASE - TOP);
  const line = points.map((point, index) => `${x(index)},${y(point.value)}`).join(" ");
  const every = Math.ceil(points.length / 5);
  const shown = hover === null ? null : points[hover];
  const last = points.at(-1);
  return (
    <div ref={ref} className="relative">
      {width && last ? (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`${name}: ${points.map((point) => point.label).join(", ")}`}
          className="block"
        >
          {[0, 0.5, 1].map((value) => (
            <GridLine key={value} y={y(value)} width={width} label={`${value * 100}`} />
          ))}
          {points.map((point, index) =>
            index % every === 0 || index === points.length - 1 ? (
              <text
                key={point.id}
                x={x(index)}
                y={HEIGHT - 4}
                textAnchor={index === points.length - 1 ? "end" : index ? "middle" : "start"}
                className="fill-ink-3 text-[11px]"
              >
                {point.at}
              </text>
            ) : null,
          )}
          <polygon
            points={`${x(0)},${BASE} ${line} ${x(points.length - 1)},${BASE}`}
            className="fill-chart opacity-10"
          />
          <polyline
            points={line}
            fill="none"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            className="stroke-chart"
          />
          {hover === null ? null : (
            <line x1={x(hover)} x2={x(hover)} y1={TOP} y2={BASE} className="stroke-ink-3" />
          )}
          {points.map((point, index) => (
            <circle
              key={point.id}
              cx={x(index)}
              cy={y(point.value)}
              r={4}
              strokeWidth={2}
              className="fill-chart stroke-group"
            />
          ))}
          <text
            x={x(points.length - 1) + 8}
            y={y(last.value) + 4}
            className="fill-foreground text-[12px] font-semibold tabular-nums"
          >
            {last.label}
          </text>
          {points.map((point, index) => (
            // biome-ignore lint/a11y/noStaticElementInteractions: a pointer shortcut; the list of runs is the keyboard way to the same run.
            <rect
              key={point.id}
              x={x(index) - step / 2}
              y={0}
              width={Math.max(step, 24)}
              height={BASE}
              fill="transparent"
              onPointerEnter={() => setHover(index)}
              onPointerLeave={() => setHover(null)}
              onClick={() => onPick(point.id)}
            />
          ))}
        </svg>
      ) : null}
      {shown && hover !== null ? (
        <Tip left={tipLeft(x(hover), width)} top={y(shown.value) - 8}>
          <strong className="tabular-nums">{shown.label}</strong>
          <div className="text-ink-2">{shown.detail}</div>
        </Tip>
      ) : null}
    </div>
  );
}

/** A clean top for an axis over `value`: 1, 2 or 5 times a power of ten, doubled to halve evenly. */
/** How many medians above the median a column may go before it stops at the top of the axis. */
const OFF_SCALE = 4;

function niceTop(value: number): number {
  const power = 10 ** Math.floor(Math.log10(Math.max(value, 1)));
  return ([1, 2, 5, 10].find((step) => step * power >= value) ?? 10) * power;
}

/** Columns in play order, one axis from zero, with the median marked. A few far above the rest
 * (a question that hung, say) would flatten everything else, so they stop at the top of the axis,
 * in another colour and counted; each one's real value is in its tooltip. */
export function ColumnChart({
  columns,
  median,
  unit,
}: {
  columns: { id: string; value: number; detail: string }[];
  median: number | null;
  unit: string;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const highest = Math.max(...columns.map((column) => column.value), 1);
  const limit = median ? median * OFF_SCALE : highest;
  const top = niceTop(Math.min(highest, limit));
  const y = (value: number) => BASE - (Math.min(value, top) / top) * (BASE - TOP);
  const above = columns.filter((column) => column.value > top).length;
  const slot = (width - AXIS_LEFT) / Math.max(1, columns.length);
  const bar = Math.max(1, Math.min(24, slot - 2));
  const left = (index: number) => AXIS_LEFT + index * slot + (slot - bar) / 2;
  const shown = hover === null ? null : columns[hover];
  const slowest = columns.reduce(
    (best, column, index) => (column.value > (columns[best]?.value ?? 0) ? index : best),
    0,
  );
  const lit = hover ?? slowest;
  return (
    <div ref={ref} className="relative">
      {width ? (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`${columns.length} questions${median ? `, median ${Math.round(median)} ${unit}` : ""}`}
          className="block"
        >
          <defs>
            <linearGradient id="lit-column" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="var(--chart)" />
              <stop offset="1" stopColor="var(--chart-3)" stopOpacity="0.55" />
            </linearGradient>
          </defs>
          {[0, top / 2, top].map((value) => (
            <GridLine key={value} y={y(value)} width={width} label={`${value}`} />
          ))}
          <text x={AXIS_LEFT} y={HEIGHT - 4} className="fill-ink-3 text-[11px]">
            First
          </text>
          <text x={width} y={HEIGHT - 4} textAnchor="end" className="fill-ink-3 text-[11px]">
            {above ? `${above} above ${top} ${unit} · Last` : "Last"}
          </text>
          {columns.map((column, index) => {
            const high = BASE - y(column.value);
            const round = Math.min(4, high, bar / 2);
            const at = left(index);
            return (
              <g
                key={column.id}
                onPointerEnter={() => setHover(index)}
                onPointerLeave={() => setHover(null)}
              >
                <rect x={at - 1} y={0} width={slot} height={BASE} fill="transparent" />
                <path
                  d={`M${at} ${BASE}V${BASE - high + round}Q${at} ${BASE - high} ${at + round} ${BASE - high}H${at + bar - round}Q${at + bar} ${BASE - high} ${at + bar} ${BASE - high + round}V${BASE}Z`}
                  className={
                    column.value > top
                      ? "fill-warning"
                      : index === lit
                        ? "fill-[url(#lit-column)]"
                        : "fill-chart-soft"
                  }
                />
                {index === lit && hover === null ? (
                  <text
                    x={at + bar / 2}
                    y={BASE - high - 5}
                    textAnchor="middle"
                    className="fill-foreground text-[11px] font-medium tabular-nums"
                  >
                    {Math.round(column.value)}
                  </text>
                ) : null}
              </g>
            );
          })}
          {median ? (
            <line
              x1={AXIS_LEFT}
              x2={width}
              y1={y(median)}
              y2={y(median)}
              className="stroke-ink-3"
              strokeWidth={1}
              strokeDasharray="3 4"
            >
              <title>{`Median ${Math.round(median)} ${unit}`}</title>
            </line>
          ) : null}
        </svg>
      ) : null}
      {shown && hover !== null ? (
        <Tip left={tipLeft(left(hover) + bar / 2, width)} top={y(shown.value) - 8}>
          <strong className="tabular-nums">
            {Math.round(shown.value)} {unit}
          </strong>
          <div className="line-clamp-2 text-ink-2">{shown.detail}</div>
        </Tip>
      ) : null}
    </div>
  );
}

/** A table of a name, a thin rate bar on one 0 to 100% track, and the count. */
export function BarChart({
  rows,
  head,
}: {
  rows: { name: string; value: number; of: number; note?: string }[];
  head: string;
}) {
  const cell = "border-t border-separator px-4 py-2.5";
  return (
    <div className="-mx-4 -mb-4 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="bg-head text-[10.5px] font-medium tracking-[0.06em] text-ink-3 uppercase">
            <th scope="col" className="px-4 py-2 text-start font-medium">
              {head}
            </th>
            <th scope="col" className="w-[42%] px-4 py-2 text-start font-medium">
              Rate
            </th>
            <th scope="col" className="px-4 py-2 text-end font-medium">
              Right
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, value, of, note }) => {
            const part = of ? (value / of) * 100 : 0;
            return (
              <tr key={name} title={note}>
                <td className={cell}>{name}</td>
                <td className={cell}>
                  <span className="flex items-center gap-2.5">
                    <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-chart-soft">
                      <span
                        className="block h-full rounded-full bg-chart"
                        style={{ width: `${part}%` }}
                      />
                    </span>
                    <span className="w-9 text-end text-[12px] text-ink-2 tabular-nums">
                      {Math.round(part)}%
                    </span>
                  </span>
                </td>
                <td className={cn(cell, "text-end whitespace-nowrap text-ink-2 tabular-nums")}>
                  {value} of {of}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const PROMPT_HEIGHT = 250;
const PROMPT_LINES = ["stroke-chart", "stroke-chart-2", "stroke-chart-4", "stroke-chart-3"];
/** A prompt this share of the one before it, or less, lost older turns. As the server reads it. */
const FOLD_SHRINKS = 0.8;

/** How big each question's prompt got, call by call. A drop is the app folding older turns, the
 * moment the raw chat leaves what the model can see. A few questions get a colour; many stay grey,
 * and the one under the pointer lights up. */
export function PromptChart({
  questions,
}: {
  questions: { id: string; name: string; sizes: number[] }[];
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<string | null>(null);
  const longest = Math.max(...questions.map((question) => question.sizes.length), 2);
  const highest = Math.max(...questions.flatMap((question) => question.sizes), 1);
  const top = niceTop(highest / 1000) * 1000;
  const base = PROMPT_HEIGHT - 22;
  const x = (index: number) => AXIS_LEFT + (index / (longest - 1)) * (width - AXIS_LEFT - 8);
  const y = (size: number) => base - (size / top) * (base - TOP);
  const few = questions.length <= PROMPT_LINES.length;
  return (
    <div ref={ref} className="flex flex-col gap-2">
      {width ? (
        <svg
          width={width}
          height={PROMPT_HEIGHT}
          role="img"
          aria-label={`Prompt size call by call for ${questions.length} questions`}
          className="block"
        >
          {[0, top / 2, top].map((value) => (
            <GridLine key={value} y={y(value)} width={width} label={`${value / 1000}k`} />
          ))}
          <text x={AXIS_LEFT} y={PROMPT_HEIGHT - 4} className="fill-ink-3 text-[11px]">
            First call
          </text>
          <text x={width} y={PROMPT_HEIGHT - 4} textAnchor="end" className="fill-ink-3 text-[11px]">
            Last
          </text>
          {questions.map((question, index) => {
            const lit = few || hover === question.id;
            const color = few ? PROMPT_LINES[index] : lit ? "stroke-chart" : "stroke-chart-soft";
            const line = question.sizes.map((size, at) => `${x(at)},${y(size)}`).join(" ");
            return (
              <g
                key={question.id}
                onPointerEnter={() => setHover(question.id)}
                onPointerLeave={() => setHover(null)}
              >
                <title>{question.name}</title>
                <polyline
                  points={line}
                  fill="none"
                  strokeWidth={lit ? 2 : 1.5}
                  strokeLinejoin="round"
                  className={color}
                />
                {lit
                  ? question.sizes.map((size, at) => {
                      const before = question.sizes[at - 1];
                      const first = question.sizes[0] ?? 0;
                      if (!before || size >= before * FOLD_SHRINKS || size <= first) return null;
                      return (
                        <g key={at}>
                          <circle
                            cx={x(at)}
                            cy={y(size)}
                            r={4.5}
                            strokeWidth={2}
                            className={cn("fill-group", color)}
                          />
                          <text
                            x={x(at) + 8}
                            y={y(size) + (index % 2 ? 16 : -8)}
                            className="fill-foreground text-[11px] font-medium tabular-nums"
                          >
                            {Math.round(before / 1000)}k → {Math.round(size / 1000)}k
                          </text>
                        </g>
                      );
                    })
                  : null}
              </g>
            );
          })}
        </svg>
      ) : null}
      {few ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
          {questions.map((question, index) => (
            <span key={question.id} className="flex min-w-0 items-center gap-1.5">
              <svg width="8" height="8" aria-hidden className="shrink-0">
                <line x1="0" x2="8" y1="4" y2="4" strokeWidth="4" className={PROMPT_LINES[index]} />
              </svg>
              <span className="max-w-[260px] truncate">{question.name}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const GAUGE_RADIUS = 76;
const GAUGE_WIDTH = 24;

/** How the run's questions came out: the shares on a half circle, the counts above it. */
export function Outcomes({
  parts,
  headline,
}: {
  parts: { name: string; value: number; color: string; dot: string }[];
  headline: string;
}) {
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const box = (GAUGE_RADIUS + GAUGE_WIDTH / 2) * 2;
  const middle = box / 2;
  const arc = `M${middle - GAUGE_RADIUS} ${middle}A${GAUGE_RADIUS} ${GAUGE_RADIUS} 0 0 1 ${middle + GAUGE_RADIUS} ${middle}`;
  let start = 0;
  return (
    <div className="flex flex-col gap-3">
      <dl className="m-0 flex flex-wrap gap-x-6 gap-y-2">
        {parts.map((part) => (
          <div key={part.name} className="flex flex-col gap-0.5">
            <dt className="flex items-center gap-1.5 text-[12px] text-ink-2">
              <span className={cn("h-3 w-0.5 rounded-full", part.dot)} />
              {part.name}
            </dt>
            <dd className="m-0 text-[16px] font-semibold tabular-nums">{part.value}</dd>
          </div>
        ))}
      </dl>
      <svg
        viewBox={`0 0 ${box} ${middle + 4}`}
        role="img"
        aria-label={parts.map((part) => `${part.name} ${part.value}`).join(", ")}
        className="mx-auto block w-full max-w-[260px]"
      >
        <defs>
          <pattern
            id="not-played"
            width="5"
            height="5"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="5" height="5" className="fill-chart-soft" />
            <line x1="0" x2="0" y1="0" y2="5" strokeWidth="2" className="stroke-group" />
          </pattern>
        </defs>
        {total
          ? parts.map((part) => {
              const length = (part.value / total) * 100;
              const offset = start;
              start += length;
              return part.value ? (
                <path
                  key={part.name}
                  d={arc}
                  pathLength={100}
                  fill="none"
                  strokeWidth={GAUGE_WIDTH}
                  strokeDasharray={`${Math.max(length - 0.8, 0.4)} 100`}
                  strokeDashoffset={-offset}
                  className={part.color}
                />
              ) : null;
            })
          : null}
        <text
          x={middle}
          y={middle - 6}
          textAnchor="middle"
          className="fill-foreground text-[26px] font-semibold tabular-nums"
        >
          {headline}
        </text>
      </svg>
    </div>
  );
}

const FLOW_COLUMN = 56;
const FLOW_GAP = 3;
const FLOW_MOST = 5;
const FLOW_FILLS = ["fill-chart", "fill-chart-3", "fill-chart-4", "fill-chart-2", "fill-chart-5"];
/** The blocks in a column: one a question while they fit, then an even five. */
const blocks = (value: number) => Array.from({ length: Math.min(value, FLOW_MOST) });

/** How far something got, stage by stage: a stack for each, with a soft band to the next. */
export function FlowChart({ steps }: { steps: { name: string; value: number; of: number }[] }) {
  const [ref, width] = useWidth();
  const top = 22;
  const base = HEIGHT - 26;
  const slot = width / Math.max(1, steps.length);
  const column = Math.min(FLOW_COLUMN, slot * 0.55);
  const x = (index: number) => slot * index + (slot - column) / 2;
  const y = (step: { value: number; of: number }) =>
    base - (step.of ? step.value / step.of : 0) * (base - top);
  return (
    <div ref={ref}>
      {width ? (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={steps.map((step) => `${step.name} ${step.value} of ${step.of}`).join(", ")}
          className="block"
        >
          {steps.slice(1).map((step, index) => {
            const before = steps[index];
            if (!before) return null;
            const from = x(index) + column;
            const to = x(index + 1);
            const bend = (to - from) / 2;
            return (
              <path
                key={step.name}
                d={`M${from} ${y(before)}C${from + bend} ${y(before)} ${to - bend} ${y(step)} ${to} ${y(step)}V${base}H${from}Z`}
                className="fill-chart opacity-[0.08]"
              />
            );
          })}
          {steps.map((step, index) => {
            const high = base - y(step);
            return (
              <g key={step.name}>
                <title>{`${step.name}: ${step.value} of ${step.of}`}</title>
                <rect
                  x={x(index)}
                  y={top}
                  width={column}
                  height={base - top}
                  rx={5}
                  className="fill-chart-soft opacity-60"
                />
                {blocks(step.value).map((_, block) => {
                  const count = blocks(step.value).length;
                  const each = (high - FLOW_GAP * (count - 1)) / count;
                  return each > 0 ? (
                    <rect
                      key={block}
                      x={x(index)}
                      y={base - (block + 1) * each - block * FLOW_GAP}
                      width={column}
                      height={each}
                      rx={4}
                      className={FLOW_FILLS[block % FLOW_FILLS.length]}
                    />
                  ) : null;
                })}
                <text
                  x={x(index) + column / 2}
                  y={y(step) - 6}
                  textAnchor="middle"
                  className="fill-foreground text-[11.5px] font-medium tabular-nums"
                >
                  {step.of ? `${Math.round((step.value / step.of) * 100)}%` : "–"}
                </text>
                <text
                  x={x(index) + column / 2}
                  y={HEIGHT - 8}
                  textAnchor="middle"
                  className="fill-ink-2 text-[11px]"
                >
                  {step.name}
                </text>
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}
