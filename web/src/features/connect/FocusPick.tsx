import { Check } from "lucide-react";
import { cn } from "../../lib/cn";
import { count } from "../../lib/format";
import type { FocusChoice } from "../../lib/schema";

/** The answer that keeps every agent (no `code.focus`). */
export const ALL = "*";

/**
 * A question answered from a pick list ("Which agent do you want to test?"): each choice its
 * name, where it is defined in mono, and how often real traces used it. One click answers.
 * `value` is the choice picked so far; none picked shows "All of them" as the one that holds.
 */
export function FocusPick({
  label,
  choices,
  value,
  disabled,
  onPick,
}: {
  label: string;
  choices: FocusChoice[];
  value?: string;
  disabled?: boolean;
  onPick: (value: string) => void;
}) {
  const picked = value || ALL;
  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <span id="focus-pick-label">{label}</span>
      <ul
        aria-labelledby="focus-pick-label"
        className="m-0 flex max-h-72 list-none flex-col overflow-y-auto rounded-xl border border-rule bg-group p-0"
      >
        {choices.map((choice) => {
          const on = choice.value === picked;
          return (
            <li key={choice.value} className="border-b border-rule last:border-b-0">
              <button
                type="button"
                aria-pressed={on}
                disabled={disabled}
                onClick={() => onPick(choice.value)}
                className={cn(
                  "flex w-full min-w-0 items-center gap-3 px-3 py-2 text-left hover:bg-sunk focus-visible:outline-2 focus-visible:outline-tint disabled:opacity-50",
                  on && "bg-tint-soft",
                )}
              >
                <span className="min-w-0 flex-1 truncate font-medium">{choice.label}</span>
                {choice.where ? (
                  <span className="min-w-0 truncate font-mono text-[12px] text-ink-3">
                    {choice.where}
                  </span>
                ) : null}
                <span className="shrink-0 text-[13px] text-ink-2">
                  {choice.value === ALL
                    ? choice.hint
                    : choice.uses === null
                      ? count(choice.tools, "tool")
                      : count(choice.uses, "use")}
                </span>
                <Check
                  aria-hidden
                  className={cn("size-4 shrink-0 text-tint-ink", on ? "visible" : "invisible")}
                />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
