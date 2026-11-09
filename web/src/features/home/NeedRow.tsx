import type { ReactNode } from "react";
import { StatusDot } from "../../ui/status";
import type { Tone } from "./overview";

/** A row's mark: the outcome dot, or "New" for something offered. Its title says it in words. */
export function Lead({ tone }: { tone: Tone }) {
  if (tone === "new")
    return (
      <span className="inline-flex h-5 items-center rounded-[5px] bg-tint-soft px-[7px] text-[11px] font-semibold text-tint-ink">
        New
      </span>
    );
  return <StatusDot state={tone} />;
}

/** One row of Needs you. `children` is what the row opens into once its action starts. */
export function NeedRow({
  tone,
  title,
  line,
  action,
  children,
  testId,
  label,
}: {
  tone: Tone;
  title: ReactNode;
  line?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  testId?: string;
  label?: string;
}) {
  return (
    <li
      className="flex items-start gap-4 border-t border-separator px-5 py-3 text-[14px]"
      data-testid={testId}
      data-tone={tone}
      aria-label={label}
    >
      <span className="flex w-9 shrink-0 justify-center pt-[1px]" aria-hidden>
        <span className="flex h-[22px] items-center">
          <Lead tone={tone} />
        </span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
          <div className="flex min-w-0 flex-1 basis-[280px] flex-col">
            <span className="font-semibold">{title}</span>
            {line ? <div className="text-[13px] text-ink-2">{line}</div> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </div>
        {children}
      </div>
    </li>
  );
}
