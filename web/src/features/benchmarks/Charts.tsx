import type { ReactNode } from "react";
import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "../../lib/cn";

/** A chart's card: what it plots and in what unit. */
export function ChartPanel({
  title,
  unit,
  children,
}: {
  title: string;
  unit: string;
  children: ReactNode;
}) {
  return (
    <section className="glass flex min-w-0 flex-col gap-3 rounded-2xl p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h3 className="m-0 text-[13.5px] font-semibold">{title}</h3>
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
const TOP = 12;
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
  const right = width - 34;
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
                  className={cn(
                    column.value > top ? "fill-warning" : "fill-chart",
                    hover !== null && hover !== index && "opacity-40",
                  )}
                />
              </g>
            );
          })}
          {median ? (
            <line
              x1={AXIS_LEFT}
              x2={width}
              y1={y(median)}
              y2={y(median)}
              className="stroke-ink-2"
              strokeWidth={1}
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

/** Horizontal bars on one 0 to 100% axis, each with its count at the end. */
export function BarChart({
  rows,
}: {
  rows: { name: string; value: number; of: number; note?: string }[];
}) {
  const [hover, setHover] = useState<string | null>(null);
  const grid =
    "grid grid-cols-[minmax(0,136px)_minmax(0,1fr)_50px] items-center gap-x-3 max-md:grid-cols-[minmax(0,118px)_minmax(0,1fr)_50px]";
  return (
    <div className="flex flex-col text-[13px]">
      {rows.map(({ name, value, of, note }) => {
        const part = of ? (value / of) * 100 : 0;
        return (
          <div
            key={name}
            className={cn(grid, "py-1.5")}
            onPointerEnter={() => setHover(name)}
            onPointerLeave={() => setHover(null)}
          >
            <span className="min-w-0 truncate text-ink-2">{name}</span>
            <div className="relative h-3">
              <div className="absolute inset-y-[-6px] start-0 w-px bg-separator" />
              <div className="absolute inset-y-[-6px] start-1/2 w-px bg-separator" />
              <div className="absolute inset-y-[-6px] end-0 w-px bg-separator" />
              {part ? (
                <div
                  className={cn(
                    "relative h-full rounded-e-[4px] bg-chart",
                    hover && hover !== name && "opacity-40",
                  )}
                  style={{ width: `${part}%` }}
                />
              ) : null}
              {hover === name ? (
                <Tip left={`${Math.max(20, Math.min(80, part))}%`} top={-6}>
                  <strong className="tabular-nums">{Math.round(part)}%</strong>
                  <div className="text-ink-2">{note ?? `${value} of ${of}`}</div>
                </Tip>
              ) : null}
            </div>
            <span className="text-end text-[12px] whitespace-nowrap text-ink-2 tabular-nums">
              {value} of {of}
            </span>
          </div>
        );
      })}
      <div className={cn(grid, "pt-1 text-[11px] text-ink-3 tabular-nums")}>
        <span />
        <div className="relative h-4">
          <span className="absolute start-0">0</span>
          <span className="absolute start-1/2 -translate-x-1/2">50</span>
          <span className="absolute end-0">100</span>
        </div>
        <span />
      </div>
    </div>
  );
}
