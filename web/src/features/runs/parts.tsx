import type { ComponentProps, ReactNode } from "react";
import { cn } from "../../lib/cn";

// The run screens' small pieces, as docs/mockups/design-studio.html draws them: a white card on a
// hairline, a chip of plain words, a pill that says how a run came out, a notice in a soft wash.
// No icons on any of them.

/** A white card on a hairline. */
export function Card({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "min-w-0 rounded-2xl border border-rule bg-group shadow-[0_1px_2px_var(--shadow)]",
        className,
      )}
      {...props}
    />
  );
}

/** A card's heading line: its title, then anything at the end. */
export function CardHead({ title, aside }: { title: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex min-h-[54px] flex-wrap items-center gap-x-3 gap-y-2 border-b border-rule px-5 py-2">
      <h2 className="m-0 text-[15px] leading-[1.3] font-semibold">{title}</h2>
      {aside ? <div className="ms-auto flex items-center gap-2">{aside}</div> : null}
    </div>
  );
}

/** A fact in a few words: "Today 11:26 · 6 min". `tint` for how the run plays, `mono` for code. */
export function Chip({
  children,
  tint,
  mono,
  title,
  className,
  ...rest
}: {
  children: ReactNode;
  tint?: boolean;
  mono?: boolean;
  title?: string;
  className?: string;
} & Omit<ComponentProps<"span">, "children">) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-7 max-w-full items-center truncate rounded-full border px-2.5 text-[13px] whitespace-nowrap",
        tint ? "border-tint-soft-2 bg-tint-soft text-tint-ink" : "border-rule bg-group text-ink-2",
        mono && "font-mono text-xs",
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}

export type Tone = "pass" | "fail" | "unjudged" | "tint" | "neutral";

const PILL: Record<Tone, string> = {
  pass: "bg-pass-soft text-pass",
  fail: "bg-fail-soft text-fail",
  unjudged: "bg-unj-soft text-unj",
  tint: "bg-tint-soft text-tint-ink",
  neutral: "bg-sunk-2 text-ink-2",
};

/** How a run came out, as a word on a soft wash: "Stopped early". */
export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      data-testid="run-state"
      className={cn(
        "inline-flex h-7 shrink-0 items-center rounded-full px-2.5 text-[13px] font-medium",
        PILL[tone],
      )}
    >
      {children}
    </span>
  );
}

const NOTICE = {
  fail: "bg-fail-soft",
  amber: "bg-unjudged-soft",
  neutral: "bg-sunk",
} as const;

/** A plain notice in a soft wash: why a run stopped, what the code may not be. */
export function Notice({
  tone,
  className,
  ...props
}: { tone: keyof typeof NOTICE } & ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "min-w-0 rounded-[10px] px-4 py-3 text-sm leading-[1.5] text-ink",
        NOTICE[tone],
        className,
      )}
      {...props}
    />
  );
}

/** A small "Not verified" pill, in sentence case, for a model's claim nothing has checked. */
export function Unverified({ children = "Not verified" }: { children?: ReactNode }) {
  return (
    <span className="inline-flex h-[22px] shrink-0 items-center rounded-full bg-sunk-2 px-2 text-[13px] text-ink-2">
      {children}
    </span>
  );
}

/** One part of a bar of outcomes: how many, and which outcome. */
export type Part = { n: number; tone: "pass" | "fail" | "unjudged" | "none" };

const BAR: Record<Part["tone"], string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  unjudged: "bg-unjudged",
  none: "bg-rule-strong",
};
const STROKE: Record<Part["tone"], string> = {
  pass: "stroke-pass",
  fail: "stroke-fail",
  unjudged: "stroke-unjudged",
  none: "stroke-rule-strong",
};

/** The swatch of an outcome in a key: a small square of its colour. */
export function Swatch({ tone }: { tone: Part["tone"] }) {
  return (
    <i aria-hidden className={cn("inline-block size-[9px] shrink-0 rounded-[2px]", BAR[tone])} />
  );
}

/** Outcomes as one bar of coloured parts, each as wide as its share of `total`. */
export function PartsBar({
  parts,
  total,
  className,
}: {
  parts: readonly Part[];
  total: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn("flex h-2.5 overflow-hidden rounded-[5px] bg-sunk-2", className)}
    >
      {total
        ? parts
            .filter((part) => part.n > 0)
            .map((part) => (
              <i
                key={part.tone}
                className={cn("block h-full", BAR[part.tone])}
                style={{ width: `${(part.n / total) * 100}%` }}
              />
            ))
        : null}
    </span>
  );
}

const R = 15.5;
const ROUND = 2 * Math.PI * R;

/** The outcomes as a small ring, passes then fails then those nobody could judge, with
 * "passed/total" in the middle. Parts that didn't play are the ring's own track. */
export function Ring({
  parts,
  total,
  label,
}: {
  parts: readonly Part[];
  total: number;
  label: string;
}) {
  let start = 0;
  const arcs = total
    ? parts
        .filter((part) => part.n > 0 && part.tone !== "none")
        .map((part) => {
          const length = (part.n / total) * ROUND;
          const arc = { part, length, start };
          start += length;
          return arc;
        })
    : [];
  const passed = parts.find((part) => part.tone === "pass")?.n ?? 0;
  return (
    <svg viewBox="0 0 36 36" role="img" aria-label={label} className="size-[92px] shrink-0">
      <circle cx="18" cy="18" r={R} fill="none" strokeWidth="4" className="stroke-sunk-2" />
      {arcs.map(({ part, length, start: from }) => (
        <circle
          key={part.tone}
          cx="18"
          cy="18"
          r={R}
          fill="none"
          strokeWidth="4"
          strokeDasharray={`${length} ${ROUND - length}`}
          strokeDashoffset={-from}
          transform="rotate(-90 18 18)"
          className={STROKE[part.tone]}
        />
      ))}
      <text
        x="18"
        y="21"
        textAnchor="middle"
        fontSize="9"
        fontWeight="600"
        className="fill-ink font-sans tabular-nums"
      >
        {`${passed}/${total}`}
      </text>
    </svg>
  );
}

/** A key–value list: the run's details, label on the start, value on the end. */
export function Facts({ children }: { children: ReactNode }) {
  return (
    <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-[18px] gap-y-2.5 text-sm">
      {children}
    </dl>
  );
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-3">{label}</dt>
      <dd className="m-0 min-w-0 text-end [overflow-wrap:anywhere]">{children}</dd>
    </>
  );
}
