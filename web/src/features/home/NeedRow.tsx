import { TriangleAlert, X } from "lucide-react";
import type { ReactNode } from "react";
import { IconBox } from "../shared/kit";
import type { Tone } from "./overview";

/** A row's mark: a cross on red for what failed, a warning on amber for what may be wrong, or
 * "New" for something offered. Its title says it in words. */
export function Lead({ tone }: { tone: Tone }) {
  if (tone === "new")
    return (
      <span className="inline-flex h-6 items-center rounded-[7px] bg-tint-soft px-2 text-xs font-semibold text-tint-ink">
        New
      </span>
    );
  return (
    <span title={tone === "fail" ? "Failed" : "May be wrong"}>
      <IconBox tone={tone === "fail" ? "bad" : "warn"}>
        {tone === "fail" ? <X /> : <TriangleAlert />}
      </IconBox>
    </span>
  );
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
      className="flex items-start gap-3.5 border-rule px-5 py-3.5 not-first:border-t"
      data-testid={testId}
      data-tone={tone}
      aria-label={label}
    >
      <span className="flex w-9 shrink-0 justify-center" aria-hidden>
        <Lead tone={tone} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex min-w-0 flex-1 basis-[280px] flex-col">
            <b className="font-medium">{title}</b>
            {line ? <div className="text-sm text-ink-2">{line}</div> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </div>
        {children}
      </div>
    </li>
  );
}
