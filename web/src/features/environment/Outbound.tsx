import { Lock } from "lucide-react";
import { useState } from "react";
import { ApiError, appApi, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { Outbound } from "../../lib/schema";
import { StatusDot } from "../../ui/status";
import { Card, Switch } from "../connect/kit";

/** A setting's kind, as the switch's line names it. */
const KINDS: Record<string, string> = {
  payment: "payments",
  mail: "mail",
  sms: "SMS",
  webhook: "webhook",
  storage: "storage",
  credential: "key",
};

export const outboundKind = (kind: string) => KINDS[kind] ?? kind;

/** What reaches past the copy, as one row of pills: the model and the traces always; each
 * listed setting, struck through and locked while it's blocked. */
export function OutboundRow({ outbound }: { outbound: readonly Outbound[] }) {
  return (
    <ul
      className="m-0 flex list-none flex-wrap items-center gap-2 p-0"
      aria-label="Leaves the copy"
    >
      {["model", "traces"].map((name) => (
        <li
          key={name}
          className="inline-flex h-[30px] items-center gap-2 rounded-full border border-rule px-3 text-sm"
        >
          <StatusDot state="pass" label="Allowed" />
          {name}
        </li>
      ))}
      {outbound.map((entry) => (
        <li
          key={entry.name}
          data-blocked={entry.allowed ? undefined : ""}
          title={outboundKind(entry.kind)}
          className={cn(
            "inline-flex h-[30px] items-center gap-2 rounded-full border px-3 font-mono text-[13px]",
            entry.allowed ? "border-rule" : "border-transparent bg-fail-soft text-fail",
          )}
        >
          {entry.allowed ? (
            <StatusDot state="pass" label="Allowed" />
          ) : (
            <Lock role="img" aria-label="Blocked" className="size-[15px]" />
          )}
          {entry.allowed ? entry.name : <s>{entry.name}</s>}
        </li>
      ))}
    </ul>
  );
}

/** "Leaves the copy": the model and traces, then a switch per listed setting. Each starts empty
 * on the copy; a switch allows one, and is refused while a run plays or testing is set up. */
export function OutboundCard({
  target,
  outbound,
  locked,
  onChange,
}: {
  target: string;
  outbound: readonly Outbound[];
  locked: boolean;
  onChange: (outbound: Outbound[]) => void;
}) {
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const flip = async (name: string, allowed: boolean) => {
    setSaving(name);
    setError(null);
    try {
      onChange((await appApi.setOutbound(target, name, allowed)).outbound);
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409 && !failure.message
          ? "Busy now: a run or the setup. Try when it's done."
          : errorText(failure, "Couldn't change it."),
      );
    }
    setSaving(null);
  };
  return (
    <Card aria-label="Leaves the copy">
      <div className="flex flex-col gap-3.5 p-5 max-md:p-4">
        <h2 className="m-0 text-[15px] font-semibold">Leaves the copy</h2>
        {[
          ["Model provider", "model"],
          ["Traces to evalution", "traces"],
        ].map(([label]) => (
          <div key={label} className="flex items-center gap-2.5">
            <StatusDot state="pass" label="Allowed" />
            <span>{label}</span>
            <span className="ms-auto">
              <Switch on label={label as string} disabled onChange={() => {}} />
            </span>
          </div>
        ))}
        {outbound.map((entry) => (
          <div key={entry.name} className="flex min-w-0 items-center gap-2.5">
            {entry.allowed ? (
              <StatusDot state="pass" label="Allowed" />
            ) : (
              <Lock role="img" aria-label="Blocked" className="size-[15px] shrink-0 text-fail" />
            )}
            <span
              className={cn(
                "min-w-0 truncate font-mono text-[13px]",
                !entry.allowed && "text-ink-3",
              )}
              title={entry.name}
            >
              {entry.name}
            </span>
            <span className="text-sm text-ink-3">{outboundKind(entry.kind)}</span>
            <span className="ms-auto">
              <Switch
                on={entry.allowed}
                label={entry.name}
                disabled={locked || saving !== null}
                onChange={(on) => void flip(entry.name, on)}
              />
            </span>
          </div>
        ))}
        {outbound.length ? null : <p className="m-0 text-sm text-ink-3">No live keys found</p>}
        {error ? (
          <p role="alert" className="m-0 text-sm text-fail">
            {error}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
