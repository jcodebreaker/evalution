import type { ComponentProps, ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { TryOutcome, Verdict } from "../../lib/schema";
import { verdictWord } from "../../lib/verdict";

// The pieces of docs/mockups/evalution-next.html the result screens share: the card, its head,
// chips, tags, the segmented control, tabs, the verdict dot, the last-ten-runs cells, a key/value
// list, a big number with its change, and a meter. Every colour is a token from index.css.

/** A screen's head: its title once, a line under it, its fact chips, and its actions. */
export function PageHead({
  title,
  sub,
  chips,
  actions,
  after,
}: {
  title: ReactNode;
  sub?: ReactNode;
  chips?: ReactNode;
  actions?: ReactNode;
  /** Beside the title on its line: a verdict chip, say. */
  after?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start gap-3.5">
      <div className="flex min-w-0 flex-[1_1_320px] flex-col">
        <div className="flex min-w-0 flex-wrap items-center gap-2.5">
          <h1 className="m-0 min-w-0 text-[28px] leading-[1.15] font-semibold tracking-[-0.025em] [overflow-wrap:anywhere] max-md:text-2xl">
            {title}
          </h1>
          {after}
        </div>
        {sub ? <p className="m-0 mt-1 text-ink-2">{sub}</p> : null}
        {chips ? (
          <div className="mt-2.5 flex flex-wrap gap-1.5" data-testid="facts">
            {chips}
          </div>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/** The card: white on a hairline, radius 16, one soft shadow. */
export function Panel({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "min-w-0 overflow-hidden rounded-2xl border border-rule bg-group shadow-[0_1px_2px_var(--shadow)]",
        className,
      )}
      {...props}
    />
  );
}

/** A card's head line: its title, then what sits at its end. */
export function PanelHead({
  title,
  children,
  className,
}: {
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-[54px] flex-wrap items-center gap-2.5 border-b border-rule px-5 py-2",
        className,
      )}
    >
      {title ? <h2 className="m-0 text-[15px] leading-tight font-semibold">{title}</h2> : null}
      {children}
    </div>
  );
}

/** A small grey heading: `h3` of the mock-up. */
export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cn("m-0 text-[13px] font-medium text-ink-3", className)}>{children}</h3>;
}

export type ChipTone = "plain" | "tint" | "warn" | "ok" | "no";

const CHIP: Record<ChipTone, string> = {
  plain: "border-rule bg-group text-ink-2",
  tint: "border-tint-soft-2 bg-tint-soft text-tint-ink",
  warn: "border-transparent bg-unj-soft text-unj",
  ok: "border-transparent bg-pass-soft text-pass",
  no: "border-transparent bg-fail-soft text-fail",
};

/** A fact as a rounded chip. */
export function Chip({
  tone = "plain",
  mono,
  title,
  className,
  children,
}: {
  tone?: ChipTone;
  mono?: boolean;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-7 max-w-full shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[13px] whitespace-nowrap [&_svg]:size-[15px]",
        mono && "font-mono text-xs",
        CHIP[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export type TagTone = "plain" | "tool" | "llm" | "agent" | "bad" | "ok" | "warn" | "miss";

const TAG: Record<TagTone, string> = {
  plain: "bg-sunk text-ink-2",
  tool: "bg-k-tool-soft text-k-tool",
  llm: "bg-tint-soft text-tint-ink",
  agent: "bg-k-agent-soft text-k-agent",
  bad: "bg-fail-soft text-fail",
  ok: "bg-pass-soft text-pass",
  warn: "bg-unj-soft text-unj",
  miss: "bg-transparent text-fail shadow-[inset_0_0_0_1.5px_var(--fail)]",
};

/** Machine text as a small tag: a tool's name, a file, `Page`. */
export function Tag({
  tone = "plain",
  never,
  title,
  className,
  children,
}: {
  tone?: TagTone;
  /** A call the test says never to make: struck through. */
  never?: boolean;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-6 max-w-full min-w-0 shrink-0 items-center gap-1 rounded-[7px] px-2 font-mono text-[13px] font-medium whitespace-nowrap",
        TAG[tone],
        never && "line-through",
        className,
      )}
    >
      <span className="truncate">{children}</span>
    </span>
  );
}

/** The segmented control: a few choices on a sunk track, the picked one lifted. */
export function Seg<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: { value: T; label: ReactNode; count?: number; disabled?: boolean; title?: string }[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <fieldset
      aria-label={label}
      className={cn(
        "m-0 inline-flex max-w-full gap-0.5 overflow-x-auto rounded-[11px] border-0 bg-sunk p-[3px]",
        className,
      )}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          disabled={option.disabled}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={cn(
            "inline-flex h-8 items-center gap-[7px] rounded-lg px-3 text-sm font-medium whitespace-nowrap text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-tint disabled:opacity-45",
            option.value === value && "bg-group text-ink shadow-[0_1px_2px_var(--shadow-2)]",
          )}
        >
          {option.label}
          {option.count === undefined ? null : (
            <>
              {" "}
              <span className="font-mono text-xs text-ink-3">{option.count}</span>
            </>
          )}
        </button>
      ))}
    </fieldset>
  );
}

/** Tabs on a hairline: the picked one underlined in violet. */
export function Tabs<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: { value: T; label: ReactNode; count?: number; disabled?: boolean }[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("flex gap-[22px] overflow-x-auto border-b border-rule", className)}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
          className={cn(
            "-mb-px inline-flex h-11 items-center gap-[7px] border-b-2 border-transparent font-medium whitespace-nowrap text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-tint disabled:text-ink-3",
            option.value === value && "border-tint text-ink",
          )}
        >
          {option.label}
          {option.count === undefined ? null : (
            <>
              {" "}
              <span className="font-mono text-xs text-ink-3">{option.count}</span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}

const DOT: Record<Verdict | "none" | "go", string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  unjudged: "bg-unj",
  flaky: "bg-[linear-gradient(90deg,var(--pass)_50%,var(--fail)_50%)]",
  none: "shadow-[inset_0_0_0_1.5px_var(--rule-strong)]",
  go: "animate-pulse bg-tint",
};

const DOT_SAID: Record<Verdict | "none" | "go", string> = {
  pass: verdictWord("pass"),
  fail: verdictWord("fail"),
  unjudged: verdictWord("unjudged"),
  flaky: verdictWord("flaky"),
  none: "Not run",
  go: "Running",
};

/** A verdict as the mock-up's 10 px dot: flaky is half pass and half fail; hollow, not run. */
export function Dot({
  verdict,
  label,
  className,
}: {
  verdict: Verdict | "none" | "go" | null;
  label?: string;
  className?: string;
}) {
  const state = verdict ?? "none";
  return (
    <span
      role="img"
      aria-label={label ?? DOT_SAID[state]}
      title={label ?? DOT_SAID[state]}
      className={cn("inline-block size-2.5 shrink-0 rounded-full", DOT[state], className)}
    />
  );
}

/** A try's outcome as a verdict, for the dot. */
export const tryVerdict = (outcome: TryOutcome): Verdict =>
  outcome === "passed" ? "pass" : outcome === "failed" ? "fail" : "unjudged";

const CELL: Record<Verdict, string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  unjudged: "bg-unj",
  flaky: "bg-[linear-gradient(90deg,var(--pass)_50%,var(--fail)_50%)]",
};

/** The last runs of a test as ten small cells, oldest first; empty cells where it didn't play. */
export function Cells({
  cells,
  size = 10,
  label,
}: {
  cells: readonly (Verdict | null)[];
  size?: number;
  label: string;
}) {
  const padded: (Verdict | null)[] = [
    ...Array<null>(Math.max(0, size - cells.length)).fill(null),
    ...cells.slice(-size),
  ];
  return (
    <span
      role="img"
      aria-label={label}
      className="grid w-[150px] grid-cols-[repeat(10,1fr)] gap-[3px]"
      data-testid="cells"
    >
      {padded.map((cell, index) => (
        <i
          key={index}
          data-cell={cell ?? "none"}
          className={cn("h-[18px] rounded-[4px]", cell ? CELL[cell] : "bg-sunk-2")}
        />
      ))}
    </span>
  );
}

/** A key/value list, keys grey on the left, values to the right. */
export function Kv({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-[18px] gap-y-2.5 text-sm">
      {rows.map(([key, value], index) => (
        <div key={index} className="contents">
          <dt className="text-ink-3">{key}</dt>
          <dd className="m-0 text-end [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export type DeltaTone = "bad" | "ok" | "flat";

/** A change, as a small pill: red worse, green better, grey the same. */
export function Delta({ tone, children }: { tone: DeltaTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[13px] font-medium whitespace-nowrap",
        tone === "bad" && "bg-fail-soft text-fail",
        tone === "ok" && "bg-pass-soft text-pass",
        tone === "flat" && "bg-sunk text-ink-2",
      )}
    >
      {children}
    </span>
  );
}

/** A share as a thin bar. */
export function Meter({
  value,
  tone = "pass",
  label,
}: {
  value: number;
  tone?: "pass" | "go" | "weak";
  label?: string;
}) {
  return (
    <div
      role="img"
      aria-label={label ?? `${Math.round(value * 100)}%`}
      className="h-2 min-w-10 overflow-hidden rounded-[4px] bg-sunk-2"
    >
      <i
        className={cn(
          "block h-full rounded-[4px] transition-[width] duration-500",
          tone === "pass" && "bg-pass",
          tone === "go" && "bg-tint",
          tone === "weak" && "bg-unj",
        )}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

/** A row of a list in a card: an icon, a title and a line, and what it leads to. */
export function Item({
  icon,
  title,
  line,
  children,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  line?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-3.5 border-rule px-5 py-3.5 not-first:border-t",
        className,
      )}
    >
      {icon}
      <div className="min-w-0 flex-1">
        <b className="block font-medium">{title}</b>
        {line ? <small className="block text-sm text-ink-2">{line}</small> : null}
      </div>
      {children}
    </div>
  );
}

export type IconTone = "bad" | "warn" | "tint" | "ok" | "plain";

/** A rounded square holding an icon, in a tone. */
export function IconBox({
  tone,
  children,
  small,
}: {
  tone: IconTone;
  children: ReactNode;
  small?: boolean;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center rounded-[10px] [&_svg]:size-[18px]",
        small ? "size-[30px]" : "size-9",
        tone === "bad" && "bg-fail-soft text-fail",
        tone === "warn" && "bg-unj-soft text-unj",
        tone === "tint" && "bg-tint-soft text-tint-ink",
        tone === "ok" && "bg-pass-soft text-pass",
        tone === "plain" && "bg-sunk text-ink-2",
      )}
    >
      {children}
    </span>
  );
}

/** The table's head cell and body cell, in the mock-up's spacing. */
export const TH =
  "border-b border-rule px-5 py-2.5 text-start text-[13px] font-medium whitespace-nowrap text-ink-3";
export const TD = "border-b border-rule px-5 py-3.5 align-middle";

/** The round play button on a row: always shown, never only on hover. */
export function PlayButton({
  label,
  onClick,
  disabled,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      className="grid size-[34px] shrink-0 place-items-center rounded-full border border-rule-strong bg-group p-0 text-ink hover:border-tint hover:bg-tint hover:text-on-tint focus-visible:outline-2 focus-visible:outline-tint disabled:opacity-40 disabled:hover:border-rule-strong disabled:hover:bg-group disabled:hover:text-ink"
    >
      <svg viewBox="0 0 24 24" aria-hidden className="ms-0.5 size-3 fill-current">
        <path d="M7 4v16l13-8z" />
      </svg>
    </button>
  );
}
