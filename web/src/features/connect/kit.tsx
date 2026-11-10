import type { ComponentProps, ReactNode } from "react";
import { cn } from "../../lib/cn";

// The small parts the connect, setup, test environment and learned screens share, drawn as the
// mock-up draws them (docs/mockups/evalution-next.html): a card, its head, a chip, a switch, a
// segmented choice and the head of a screen.

/** A card: white on a hairline, a soft shadow. */
export function Card({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "min-w-0 overflow-hidden rounded-2xl border border-rule bg-group shadow-[0_1px_2px_var(--shadow)]",
        className,
      )}
      {...props}
    />
  );
}

/** A card's head: its title, then what sits on the right. */
export function CardHead({
  title,
  children,
  className,
}: {
  title: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-[54px] flex-wrap items-center gap-2.5 border-b border-rule px-5 py-2 max-md:px-4",
        className,
      )}
    >
      <h2 className="m-0 text-[15px] font-semibold tracking-[-0.005em]">{title}</h2>
      {children ? <span className="ms-auto flex items-center gap-2">{children}</span> : null}
    </div>
  );
}

export type ChipTone = "plain" | "tint" | "warn" | "ok" | "no";

const CHIP: Record<ChipTone, string> = {
  plain: "border-rule bg-group text-ink-2",
  tint: "border-tint-soft-2 bg-tint-soft text-tint-ink",
  warn: "border-transparent bg-unjudged-soft text-unjudged",
  ok: "border-transparent bg-pass-soft text-pass",
  no: "border-transparent bg-fail-soft text-fail",
};

/** A state or a fact in a pill. */
export function Chip({
  tone = "plain",
  mono,
  className,
  ...props
}: ComponentProps<"span"> & { tone?: ChipTone; mono?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border px-2.5 text-[13px] whitespace-nowrap [&_svg]:size-[15px] [&_svg]:shrink-0",
        mono && "font-mono",
        CHIP[tone],
        className,
      )}
      {...props}
    />
  );
}

/** An on/off switch. */
export function Switch({
  on,
  label,
  disabled,
  onChange,
}: {
  on: boolean;
  label: string;
  disabled?: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={cn(
        "relative h-6 w-10 shrink-0 rounded-full transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tint disabled:opacity-50",
        on ? "bg-tint" : "bg-sunk-2",
      )}
    >
      <i
        aria-hidden
        className={cn(
          "absolute top-[3px] left-[3px] size-[18px] rounded-full bg-group shadow-[0_1px_2px_var(--shadow-2)] transition-transform duration-150",
          on && "translate-x-4",
        )}
      />
    </button>
  );
}

/** One of a few, as a segmented control; each choice is a radio, so it reads as one. */
export function Seg<T extends string>({
  name,
  value,
  options,
  disabled,
  onPick,
}: {
  name: string;
  value: T;
  options: readonly { value: T; label: string; aside?: string }[];
  disabled?: boolean;
  onPick: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={name}
      className="inline-flex max-w-full gap-0.5 overflow-x-auto rounded-[11px] bg-sunk p-[3px]"
    >
      {options.map((option) => (
        <label
          key={option.value}
          title={option.aside}
          className={cn(
            "inline-flex h-8 cursor-default items-center gap-1.5 rounded-lg px-3 text-sm font-medium whitespace-nowrap has-focus-visible:outline-2 has-focus-visible:outline-tint",
            option.value === value
              ? "bg-group text-ink shadow-[0_1px_2px_var(--shadow-2)]"
              : "text-ink-2 hover:text-ink",
            disabled && "opacity-60",
          )}
        >
          <input
            type="radio"
            name={name}
            className="sr-only"
            checked={option.value === value}
            disabled={disabled}
            onChange={() => onPick(option.value)}
          />
          {option.label}
          {option.aside ? <span className="sr-only">{option.aside}</span> : null}
        </label>
      ))}
    </div>
  );
}

/** The head of a screen: its one title, what it is in a line, its chips, then its actions. */
export function ScreenHead({
  title,
  sub,
  chips,
  actions,
}: {
  title: ReactNode;
  sub?: ReactNode;
  chips?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start gap-3.5">
      <div className="min-w-0 flex-[1_1_320px]">
        <h1 className="m-0 text-[28px] leading-[1.15] font-semibold tracking-[-0.025em] [overflow-wrap:anywhere] max-md:text-[24px]">
          {title}
        </h1>
        {sub ? <p className="m-0 mt-1 text-ink-2">{sub}</p> : null}
        {chips ? <div className="mt-2.5 flex flex-wrap gap-1.5">{chips}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/** A screen's scrolling body, as wide as the mock-up's. */
export function Screen({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div
        className={cn(
          "mx-auto flex w-full max-w-[1320px] flex-col gap-5 px-8 pt-2 pb-[120px] max-md:px-4",
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
