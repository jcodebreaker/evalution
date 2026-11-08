import { useState } from "react";
import type { Health, Isolation } from "../../lib/schema";
import { useIsolation } from "../connect/Isolation";
import { Steps } from "../connect/Steps";
import { IsolateButton } from "../shared/IsolateButton";

/**
 * A plan a commit left out of date, said the way out-of-date claims are, with the button that
 * proves it again; then the rehearsal's steps as they run.
 */
export function IsolationLine({
  target,
  isolation,
}: {
  target: string;
  isolation: Health["isolation"];
}) {
  const [started, setStarted] = useState(false);
  if (!isolation && !started) return null;
  return started ? (
    <Following target={target} />
  ) : (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px] text-ink-2">
      <span>
        Isolation plan out of date
        {isolation?.stale_files.length ? `: ${isolation.stale_files.join(", ")}` : ""}
      </span>
      <IsolateButton target={target} onStarted={() => setStarted(true)} />
    </div>
  );
}

function Following({ target }: { target: string }) {
  const { data } = useIsolation(target);
  const job: Isolation["job"] = data?.job ?? null;
  if (!job) return null;
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <Steps steps={job.steps} />
      {job.error ? <p className="m-0 text-[12.5px] text-destructive">{job.error}</p> : null}
    </div>
  );
}
