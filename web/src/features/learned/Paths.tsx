import { ArrowRight } from "lucide-react";
import { Fragment } from "react";
import type { Understanding } from "../../lib/schema";
import { Chip } from "../connect/kit";

/** The usual paths through the app, the most taken first, each a meter of its share and its
 * steps; a path the code implies that real use never took says so. */
export function Paths({ paths }: { paths: Understanding["paths"] }) {
  // Shares come as fractions or as percentages.
  const fractions = paths.filter((p) => p.seen).every((p) => p.share <= 1);
  const percent = (share: number) => Math.round(fractions ? share * 100 : share);
  const ordered = [...paths.filter((p) => p.seen), ...paths.filter((p) => !p.seen)];
  return (
    <ul className="m-0 list-none p-0" aria-label="Typical paths">
      {ordered.map((path, index) => (
        <li
          key={index}
          className="flex flex-wrap items-center gap-3.5 border-rule px-5 py-3.5 not-last:border-b max-md:px-4"
        >
          <span className="w-11 font-mono text-[13px] tabular-nums">
            {path.seen ? `${percent(path.share)}%` : "—"}
          </span>
          <span aria-hidden className="h-2 w-[120px] shrink-0 overflow-hidden rounded bg-sunk-2">
            <i
              className="block h-full rounded bg-tint"
              style={{ width: `${path.seen ? percent(path.share) : 0}%` }}
            />
          </span>
          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
            {path.steps.map((step, at) => (
              <Fragment key={at}>
                {at ? (
                  <ArrowRight role="img" aria-label="then" className="size-[15px] text-ink-3" />
                ) : null}
                <Chip>{step}</Chip>
              </Fragment>
            ))}
          </span>
          {path.seen ? null : <Chip>Expected, never seen</Chip>}
        </li>
      ))}
    </ul>
  );
}
