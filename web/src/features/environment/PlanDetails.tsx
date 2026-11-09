import { CircleAlert } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { planStatusWords, storeHow, storeKind } from "../../lib/isolation";
import type { Isolation } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Group } from "../shared/group";

/**
 * The plan behind the copy, for whoever wants it: each place in plain words with where it is, what
 * the last check found it missing, the report, and undoing the last plan.
 */
export function PlanDetails({
  isolation,
  onChange,
}: {
  isolation: Isolation;
  onChange: (isolation: Isolation) => void;
}) {
  const [undoing, setUndoing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { plan } = isolation;
  const undo = async () => {
    setUndoing(true);
    setError(null);
    try {
      onChange(await api.undoIsolation(isolation.target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't undo it."));
    }
    setUndoing(false);
  };
  return (
    <div className="flex flex-col gap-3 text-[14px]" data-testid="plan-details">
      <p className="m-0">{planStatusWords(isolation)}</p>
      {plan?.stores.length ? (
        <Group>
          {plan.stores.map((store) => (
            <div
              key={store.id}
              className="flex flex-col gap-0.5 border-separator px-3.5 py-2.5 not-first:border-t"
            >
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="shrink-0 font-medium">{storeKind(store.kind)}</span>
                <span className="min-w-0 truncate font-mono text-[11.5px] text-ink-3">
                  {store.locator}
                </span>
              </div>
              <span className="text-[13px] text-ink-2">
                {storeHow(store)}
                {store.seen ? "" : " · not seen in use"}
              </span>
            </div>
          ))}
        </Group>
      ) : null}
      {plan?.exclude.length ? (
        <p className="m-0 text-ink-2">Left alone, kept as evidence: {plan.exclude.join(", ")}</p>
      ) : null}
      {plan?.leaks.length ? (
        <div className="flex flex-col gap-1">
          <p className="m-0 font-medium">Where the last check found what the plan misses</p>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {plan.leaks.map((leak) => (
              <li key={leak} className="flex items-start gap-2">
                <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
                <span className="min-w-0 font-mono text-[12.5px] break-all">{leak}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {isolation.evidence ? (
        <p className="m-0 text-[13px] text-ink-3">
          The code named {isolation.evidence.candidates} possible places; the app used{" "}
          {isolation.evidence.touched} when watched.
        </p>
      ) : null}
      {plan?.report ? (
        <pre className="m-0 font-mono text-[11.5px] whitespace-pre-wrap text-ink-2">
          {plan.report}
        </pre>
      ) : null}
      {isolation.can_undo ? (
        <Button
          variant="ghost"
          className="self-start"
          disabled={undoing}
          onClick={() => void undo()}
        >
          Undo the last plan
        </Button>
      ) : null}
      {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
    </div>
  );
}
