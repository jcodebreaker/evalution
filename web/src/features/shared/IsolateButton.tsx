import { useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Isolation } from "../../lib/schema";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";
import { Spinner } from "../../ui/loading";

/** What finding isolation spends, said before it does. */
export const ISOLATE_COST =
  "This sends two messages to evalution's own copy of your app, never to your app.";

/**
 * The one button that finds where the app keeps what it remembers and proves a plan to reset it,
 * on evalution's own copy of the app. It asks first, since it sends two messages to that copy.
 */
export function IsolateButton({
  target,
  label = "Find and prove isolation",
  disabled = false,
  onStarted,
}: {
  target: string;
  label?: string;
  disabled?: boolean;
  onStarted: (isolation: Isolation) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setAsking(false);
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.runIsolation(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't start. Nothing was sent."));
    }
    setBusy(false);
  };
  return (
    <>
      <Button variant="secondary" disabled={disabled || busy} onClick={() => setAsking(true)}>
        {busy ? <Spinner /> : null}
        {label}
      </Button>
      {error ? <span className="text-[13px] text-destructive">{error}</span> : null}
      {asking ? (
        <ConfirmDialog
          onClose={() => setAsking(false)}
          title="Find and prove isolation?"
          description={`evalution starts its own copy of your app, sends it one message to see where it keeps what it remembers, then rehearses a plan to reset each place with a second message. ${ISOLATE_COST}`}
          footer={
            <>
              <Button variant="secondary" onClick={() => setAsking(false)}>
                Cancel
              </Button>
              <Button onClick={() => void start()}>Send two messages</Button>
            </>
          }
        />
      ) : null}
    </>
  );
}
