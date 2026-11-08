import { isolatedWords, tryIsolationDetail } from "../../lib/isolation";
import type { TryIsolation } from "../../lib/schema";

/** How one try was isolated, and what the check around it found wrong, if anything. */
export function TryIsolationNote({ isolation }: { isolation: TryIsolation | null | undefined }) {
  if (!isolation) return null;
  const detail = tryIsolationDetail(isolation);
  return (
    <div className="flex flex-col gap-0.5 text-[12.5px]">
      <p className="m-0 text-ink-2">
        {isolatedWords(isolation)}
        {detail.length ? ` · ${detail.join(" · ")}` : ""}
      </p>
      {isolation.problem ? <p className="m-0 text-warning">{isolation.problem}</p> : null}
    </div>
  );
}
