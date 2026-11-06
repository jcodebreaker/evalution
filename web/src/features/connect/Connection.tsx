import type { ReactNode } from "react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";
import { Skeleton } from "../../ui/field";
import { Spinner } from "../../ui/loading";
import { KEY_LEFT_BEHIND } from "../shared/hosts";
import { CheckResult } from "./CheckResult";
import { Isolation } from "./Isolation";
import { Probe } from "./Probe";
import { TargetFields } from "./TargetFields";
import { useCheck } from "./use-check";

/** How evalution reaches an app: edit and check it, or remove the app. */
export function Connection({
  target,
  onSaved,
  onRemoved,
}: {
  target: string;
  onSaved: () => Promise<unknown>;
  onRemoved: () => void;
}) {
  const [load, reload] = useLoad(`target:${target}`, () => api.target(target));
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keyLeft, setKeyLeft] = useState(false);
  const checker = useCheck();
  const saved = load.status === "ready" ? load.data : null;
  const [targets, reloadTargets] = useLoad(`setup:${target}`, api.targets);
  const row = ready(targets)?.find((entry) => entry.name === target) ?? null;
  const change = saved?.adapter_change;

  const remove = async () => {
    try {
      await api.removeTarget(target);
      onRemoved();
    } catch (failure) {
      setRemoving(false);
      setError(errorText(failure, "Couldn't remove it."));
    }
  };
  const removeButton = (
    <Button variant="destructive" className="me-auto" onClick={() => setRemoving(true)}>
      Remove
    </Button>
  );

  let body: ReactNode;
  if (load.status === "loading") body = <Skeleton className="h-80 rounded-xl" />;
  else if (!saved) body = <p className="m-0 text-[13px] text-ink-2">Couldn't load it.</p>;
  else if (!saved.managed) {
    body = (
      <>
        <p className="m-0 text-[13px] text-ink-2">Set in evalution.yaml.</p>
        {checker.check ? <CheckResult check={checker.check} /> : null}
        {checker.error ? (
          <p className="m-0 text-[12.5px] text-destructive">{checker.error}</p>
        ) : null}
        <Button
          variant="secondary"
          className="self-end"
          disabled={checker.checking}
          onClick={() => void checker.run(target)}
        >
          {checker.checking ? <Spinner /> : null}
          Check connection
        </Button>
      </>
    );
  } else {
    body = (
      <TargetFields
        key={target}
        saved={saved}
        action="Save"
        onSave={async (next) => {
          const done = await api.updateTarget(next);
          setKeyLeft(done.secrets_dropped.length > 0);
          await Promise.all([reload(), onSaved(), reloadTargets()]);
        }}
        extra={removeButton}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {saved ? (
        <p className="m-0 text-[13px] text-ink-2">
          Connected as {saved.adapter_label}.
          {change
            ? ` Changed from ${change.from} to ${change.to}: earlier results tested the other.`
            : ""}
        </p>
      ) : null}
      {saved?.adapter_error ? (
        <p className="m-0 text-[13px] text-destructive">
          Its adapter doesn't load: {saved.adapter_error}
        </p>
      ) : null}
      <Probe target={target} checked={!!row?.checked} onChange={reloadTargets} />
      {row ? <Isolation target={row} onChange={reloadTargets} /> : null}
      {body}
      {keyLeft ? <p className="m-0 text-[12.5px] text-ink-2">Saved. {KEY_LEFT_BEHIND}</p> : null}
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
      {removing ? (
        <ConfirmDialog
          onClose={() => setRemoving(false)}
          title={`Remove ${target}?`}
          description="Its tests and runs stay on disk."
          footer={
            <>
              <Button variant="secondary" onClick={() => setRemoving(false)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={() => void remove()}>
                Remove
              </Button>
            </>
          }
        />
      ) : null}
    </div>
  );
}
