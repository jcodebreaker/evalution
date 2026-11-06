import { useEffect, useState } from "react";
import type { Scope } from "../../lib/api";
import { ApiError, api, errorText } from "../../lib/api";
import { count } from "../../lib/format";
import type { Estimate, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";

/** What a run plays: the whole set, one of its tests, or some of them. */
export type RunTarget = {
  scope: Scope;
  test?: { id: string; name: string };
  cases?: readonly string[];
  /** Tries per test, instead of each test's own. */
  tries?: number;
  /** An estimate already in hand, as saving a test with `run` returns one. */
  estimate?: Estimate;
};

/**
 * Every run starts behind this confirm: what the server estimates it costs. Run spends the
 * estimate's token, so nothing starts unconfirmed.
 */
export function RunConfirm({
  target,
  onClose,
  onStarted,
}: {
  target: RunTarget;
  onClose: () => void;
  onStarted: (run: RunSummary) => void;
}) {
  const [estimate, setEstimate] = useState<Estimate | null>(target.estimate ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const given = !!target.estimate;
  const { target: name, set } = target.scope;
  const testId = target.test?.id;
  const cases = target.cases;
  const tries = target.tries;
  useEffect(() => {
    if (given) return;
    let cancelled = false;
    api.estimate({ target: name, set }, testId, cases, tries).then(
      (found) => {
        if (!cancelled) setEstimate(found);
      },
      (failure: unknown) => {
        if (!cancelled) setError(errorText(failure, "Couldn't estimate this run."));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [given, name, set, testId, cases, tries]);

  const start = async () => {
    if (!estimate) return;
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.start(estimate.token));
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409
          ? "Another run is going. Wait for it, or stop it."
          : errorText(failure, "Couldn't start the run."),
      );
      setBusy(false);
    }
  };

  const title = target.test
    ? `Run ${target.test.name}?`
    : cases
      ? `Run ${count(cases.length, "test")}${tries ? ` with ${tries} tries each` : ""}?`
      : estimate
        ? estimate.cases === 1
          ? "Run the 1 test?"
          : `Run all ${estimate.cases} tests?`
        : "Run all tests?";

  return (
    <ConfirmDialog
      onClose={() => {
        if (!busy) onClose();
      }}
      title={title}
      description={estimate || error ? null : "…"}
      footer={
        <>
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!estimate || busy || !estimate.cases} onClick={() => void start()}>
            Run
          </Button>
        </>
      }
    >
      {estimate?.isolation_note ? (
        <p className="m-0 text-center text-[12.5px] text-ink-2">{estimate.isolation_note}</p>
      ) : null}
      {estimate?.refused.map((item) => (
        <p key={item.case} className="m-0 text-center text-[12.5px] text-warning">
          {`Left out: ${item.case}, because ${item.why}`}
        </p>
      ))}
      {error ? <p className="m-0 text-center text-[12.5px] text-destructive">{error}</p> : null}
    </ConfirmDialog>
  );
}
