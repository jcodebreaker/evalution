import { useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Probe as ProbeResult, Redraft } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { StatusDot } from "../../ui/status";
import { Fold, Group } from "../shared/group";
import { KEY_LEFT_BEHIND } from "../shared/hosts";

const TRACES: Record<string, string> = {
  complete: "Arrived",
  partial: "Some arrived",
  empty: "None arrived",
  no_source: "Not connected",
  unread: "Couldn't be read",
};

function Line({ label, children }: { label: string; children: string }) {
  return (
    <div className="flex items-start gap-2.5 border-separator px-3.5 py-2.5 text-[14px] not-first:border-t">
      <span className="w-16 shrink-0 font-medium">{label}</span>
      <span className="min-w-0 break-words text-ink-2">{children}</span>
    </div>
  );
}

/** One real message through the saved app: its reply, its traces, and what to do when it fails. */
export function Probe({
  target,
  checked,
  onChange,
}: {
  target: string;
  /** A message has passed on these settings. */
  checked: boolean;
  onChange: () => Promise<unknown>;
}) {
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [keyLeft, setKeyLeft] = useState(false);
  const [redraft, setRedraft] = useState<Redraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const attempt = async (work: () => Promise<void>, fallback: string) => {
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(errorText(failure, fallback));
    }
  };
  const send = async () => {
    setBusy(true);
    setRedraft(null);
    await attempt(async () => {
      setResult(await api.probe(target));
      await onChange();
    }, "Couldn't send the message.");
    setBusy(false);
  };
  const correct = async () => {
    setDrafting(true);
    await attempt(async () => setRedraft(await api.redraft(target)), "Couldn't correct it.");
    setDrafting(false);
  };
  const use = async (options: Record<string, unknown>) => {
    setBusy(true);
    await attempt(async () => {
      const saved = await api.target(target);
      const done = await api.updateTarget({
        name: saved.name,
        adapter: saved.adapter,
        options,
        phoenix: saved.phoenix,
        code: saved.code,
        secrets: {},
      });
      setKeyLeft(done.secrets_dropped.length > 0);
      setRedraft(null);
      setResult(await api.probe(target));
      await onChange();
    }, "Couldn't save the settings.");
    setBusy(false);
  };

  return (
    <div className="flex flex-col gap-3">
      {result ? (
        <Group>
          <div className="flex items-start gap-3 px-4 py-2.5 text-[14px] font-medium">
            <StatusDot
              state={result.ok ? "pass" : "fail"}
              label={result.ok ? "It works" : "It didn't work"}
              className="mt-[7px]"
            />
            {result.ok ? `It works, in ${result.seconds} s` : "It didn't work"}
          </div>
          {result.reply ? <Line label="Reply">{result.reply}</Line> : null}
          <Line label="Traces">{TRACES[result.traces] ?? result.traces}</Line>
          {result.tools ? <Line label="Tools">{String(result.tools)}</Line> : null}
          {result.problems.map((problem) => (
            <div
              key={problem}
              className="border-t border-separator px-3.5 py-2.5 text-[14px] break-words text-fail"
            >
              {problem}
            </div>
          ))}
          {result.warnings.map((warning) => (
            <div
              key={warning}
              className="border-t border-separator px-3.5 py-2.5 text-[14px] break-words text-ink-2"
            >
              {warning}
            </div>
          ))}
          {keyLeft ? (
            <div className="border-t border-separator px-3.5 py-2.5 text-[14px] text-ink-2">
              {KEY_LEFT_BEHIND}
            </div>
          ) : null}
          {result.next ? (
            <div className="border-t border-separator px-3.5 py-2.5 text-[14px]">{result.next}</div>
          ) : null}
        </Group>
      ) : null}

      {redraft ? (
        <Group>
          {redraft.tried.map((round, index) => (
            <div
              key={index}
              className="flex flex-col gap-1 border-separator px-3.5 py-2.5 text-[14px] not-first:border-t"
            >
              <span className="font-medium">
                Try {index + 1}: {round.problems.length ? "didn't work" : "worked"}
              </span>
              {round.reply ? <span className="break-words text-ink-2">{round.reply}</span> : null}
              {round.problems.map((problem) => (
                <span key={problem} className="break-words text-fail">
                  {problem}
                </span>
              ))}
              {round.settings ? (
                <Fold title="Settings">
                  <pre className="m-0 overflow-x-auto rounded-[7px] bg-window px-2.5 py-2 font-mono text-[11.5px] leading-[1.55]">
                    {JSON.stringify(round.settings, null, 2)}
                  </pre>
                </Fold>
              ) : null}
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2.5 border-t border-separator px-3.5 py-3 text-[14px]">
            {redraft.options ? (
              <Button
                size="sm"
                className="ms-auto"
                disabled={busy}
                onClick={() => void use(redraft.options ?? {})}
              >
                {busy ? <Spinner /> : null}
                Use these and send one message
              </Button>
            ) : (
              <span className="text-ink-2">Stopped there. Set it under Details.</span>
            )}
          </div>
        </Group>
      ) : null}

      {error ? <p className="m-0 text-[13px] text-fail">{error}</p> : null}

      <div className="flex flex-wrap items-center gap-2">
        {checked && !result ? <span className="text-[14px] text-ink-2">Passed.</span> : null}
        {result?.can_redraft && !redraft ? (
          <Button disabled={drafting || busy} onClick={() => void correct()}>
            {drafting ? (
              <Spinner className="border-primary-foreground border-r-transparent" />
            ) : null}
            Correct it, with up to 2 more messages
          </Button>
        ) : null}
        <Button variant="secondary" disabled={busy || drafting} onClick={() => void send()}>
          {busy ? <Spinner /> : null}
          Send one test message
        </Button>
      </div>
    </div>
  );
}
