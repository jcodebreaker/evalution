import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { count } from "../../lib/format";
import type { Knowledge, Understanding } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";

/** How much of what it knows may be out of date, and the one button that updates them. */
export function OutOfDate({
  target,
  knowledge,
  onUpdated,
}: {
  target: string;
  knowledge: Knowledge;
  onUpdated: (understanding: Understanding) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!knowledge.out_of_date) return null;
  const update = async () => {
    setBusy(true);
    setError(null);
    try {
      onUpdated(await api.updateKnowledge(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't update. Nothing changed."));
    }
    setBusy(false);
  };
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-ink-2">
      <span>{count(knowledge.out_of_date, "thing")} it knows may be out of date</span>
      {knowledge.can_update ? (
        <Button variant="secondary" disabled={busy} onClick={() => void update()}>
          {busy ? <Spinner /> : null}
          Update
        </Button>
      ) : null}
      {error ? <span className="text-fail">{error}</span> : null}
    </div>
  );
}
