import type { ReactNode } from "react";

const ROW =
  "grid grid-cols-[132px_minmax(0,1fr)] items-start gap-3 border-separator px-3.5 py-2 text-sm not-first:border-t max-sm:grid-cols-1 max-sm:gap-1.5";

/** A form row: its label, then its control. `group` for several controls (buttons). */
export function Row({
  label,
  hint,
  group,
  children,
}: {
  label: string;
  hint?: string;
  group?: boolean;
  children: ReactNode;
}) {
  const inner = (
    <>
      <span className="pt-1.5 text-ink-2 max-sm:pt-0">{label}</span>
      <span className="flex min-w-0 flex-col gap-1">
        {children}
        {hint ? <span className="text-sm text-ink-3">{hint}</span> : null}
      </span>
    </>
  );
  if (group) {
    return (
      <fieldset aria-label={label} className={`${ROW} min-w-0`}>
        {inner}
      </fieldset>
    );
  }
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: the control comes in as a child
    <label className={ROW}>{inner}</label>
  );
}
