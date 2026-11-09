import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Understanding } from "../../lib/schema";

function Step({ children, dashed }: { children: ReactNode; dashed?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex min-h-[26px] items-center rounded-full px-2.5 text-[13px]",
        dashed ? "border border-dashed border-separator py-0.5 text-ink-3" : "bg-bubble",
      )}
    >
      {children}
    </span>
  );
}

export function Paths({ paths }: { paths: Understanding["paths"] }) {
  const seen = paths.filter((path) => path.seen);
  const unseen = paths.filter((path) => !path.seen);
  // Shares come as fractions or as percentages.
  const percent = (share: number) =>
    Math.round(seen.every((p) => p.share <= 1) ? share * 100 : share);
  return (
    <>
      {seen.map((path, index) => (
        <div key={index} className="flex flex-wrap items-center gap-1.5 px-[18px] py-2">
          <span className="me-1 w-11 text-end text-[12.5px] text-ink-2 tabular-nums">
            {percent(path.share)}%
          </span>
          <span className="me-1.5 h-[5px] w-14 overflow-hidden rounded-full bg-bubble">
            <i className="block h-full bg-tint" style={{ width: `${percent(path.share)}%` }} />
          </span>
          {path.steps.map((step, at) => (
            <span key={at} className="inline-flex items-center gap-1.5">
              {at ? (
                <span aria-hidden className="text-[12.5px] text-ink-3">
                  →
                </span>
              ) : null}
              <Step>{step}</Step>
            </span>
          ))}
        </div>
      ))}
      {unseen.length ? (
        <p
          title={unseen.map((path) => path.steps.join(" → ")).join("\n")}
          className="m-0 truncate px-[18px] py-2 text-[12.5px] text-ink-3"
        >
          Expected but never seen: {unseen.map((path) => path.steps.join(" → ")).join(" · ")}
        </p>
      ) : null}
    </>
  );
}
