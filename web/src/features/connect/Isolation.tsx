import { CircleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { planStatusWords, storeHow, storeKind } from "../../lib/isolation";
import type { Isolation as IsolationView, Target } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Fold, Group } from "../shared/group";
import { HowIsolationLink } from "../shared/HowIsolationWorks";
import { ISOLATE_COST, IsolateButton } from "../shared/IsolateButton";
import { Steps } from "./Steps";

const KINDS = ["conversation", "memory", "files"] as const;

/** How often the page reads the job again while it finds and proves. */
export const ISOLATION_POLL_MS = 1_000;

/**
 * Isolation: where the app keeps what it remembers, how each place is reset between tries and
 * whether that is proven; then what every new test must start clean of, out of what the app
 * promises (by default nothing).
 */
export function Isolation({
  target,
  onChange,
}: {
  target: Target;
  onChange: () => Promise<unknown>;
}) {
  const [error, setError] = useState<string | null>(null);
  const toggle = async (kind: string, on: boolean) => {
    const required = on
      ? [...target.required_clean, kind]
      : target.required_clean.filter((entry) => entry !== kind);
    setError(null);
    try {
      await api.setIsolation(target.name, required);
      await onChange();
    } catch (failure) {
      setError(errorText(failure, "Couldn't save it."));
    }
  };
  return (
    <div className="flex flex-col gap-4 text-[13px]">
      <IsolationPlan target={target.name} />
      <div className="flex flex-col gap-2">
        {target.isolation_note ? <p className="m-0 text-ink-2">{target.isolation_note}</p> : null}
        <Group>
          {KINDS.map((kind) => {
            const promised = target.starts_clean.includes(kind);
            return (
              <label
                key={kind}
                className="flex items-center gap-2.5 border-separator px-3.5 py-2.5 not-first:border-t"
              >
                <input
                  type="checkbox"
                  disabled={!promised}
                  checked={target.required_clean.includes(kind)}
                  onChange={(event) => void toggle(kind, event.target.checked)}
                />
                <span className={promised ? undefined : "text-ink-3"}>
                  Every test starts with no earlier {kind}
                </span>
                {promised ? null : (
                  <span className="ms-auto text-[12px] text-ink-3">Not promised</span>
                )}
              </label>
            );
          })}
        </Group>
        {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
      </div>
    </div>
  );
}

/** The target's isolation plan, read and kept current while a job finds and proves it. */
export function useIsolation(target: string) {
  const [data, setData] = useState<IsolationView | null>(null);
  const [failed, setFailed] = useState(false);
  const read = useCallback(async () => {
    try {
      setData(await api.isolation(target));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [target]);
  useEffect(() => {
    setData(null);
    void read();
  }, [read]);
  useVisiblePoll(read, ISOLATION_POLL_MS, data?.job?.state === "running");
  return { data, failed, set: setData };
}

/**
 * What was found and how far it is proven: each place the app keeps what it remembers in plain
 * words, what moves it, what is left alone, the rehearsal's steps as they run, its report, and
 * the one button that finds and proves it.
 */
export function IsolationPlan({ target }: { target: string }) {
  const { data, failed, set } = useIsolation(target);
  const [undoing, setUndoing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!data) {
    return failed ? (
      <p className="m-0 text-ink-2">Couldn't load what was found about isolation.</p>
    ) : null;
  }
  const { plan, job } = data;
  const running = job?.state === "running";
  const undo = async () => {
    setUndoing(true);
    setError(null);
    try {
      set(await api.undoIsolation(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't undo it."));
    }
    setUndoing(false);
  };

  return (
    <div className="flex flex-col gap-3" data-testid="isolation-plan">
      <p className="m-0">
        {running ? "Finding where your app keeps what it remembers…" : planStatusWords(data)}
      </p>
      {data.status === "none" && !running ? (
        <p className="m-0 text-ink-2">
          evalution starts its own copy of your app, sends it a message to see where it keeps what
          it remembers, then proves a plan that resets each place between tests.
        </p>
      ) : null}
      {data.problem ? <p className="m-0 text-destructive">{data.problem}</p> : null}

      {job ? <Steps steps={job.steps} /> : null}
      {job?.state === "failed" && job.error ? (
        <p className="m-0 text-[12.5px] text-destructive">{job.error}</p>
      ) : null}

      {plan && !running ? (
        <>
          {plan.stores.length ? (
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
                  <span className="text-[12.5px] text-ink-2">
                    {storeHow(store)}
                    {store.seen ? "" : " · not seen in use"}
                  </span>
                </div>
              ))}
            </Group>
          ) : (
            <p className="m-0 text-ink-2">It found nowhere the app keeps what it remembers.</p>
          )}
          {plan.exclude.length ? (
            <p className="m-0 text-ink-2">
              Left alone, kept as evidence of what happened: {plan.exclude.join(", ")}
            </p>
          ) : null}
          {data.status === "failed" && plan.leaks.length ? (
            <div className="flex flex-col gap-1">
              <p className="m-0 font-medium">Where the rehearsal found what the plan misses</p>
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {plan.leaks.map((leak) => (
                  <li key={leak} className="flex items-start gap-2">
                    <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
                    <span className="min-w-0 font-mono text-[12px] break-all">{leak}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {data.evidence ? (
            <p className="m-0 text-[12.5px] text-ink-3">
              The code named {data.evidence.candidates} possible places; the app used{" "}
              {data.evidence.touched} when watched.
            </p>
          ) : null}
          {plan.report ? (
            <Fold title="Report" className="text-[12px]">
              <pre className="m-0 font-mono text-[11.5px] whitespace-pre-wrap text-ink-2">
                {plan.report}
              </pre>
            </Fold>
          ) : null}
        </>
      ) : null}

      <div className="flex flex-wrap items-center gap-2.5">
        <IsolateButton
          target={target}
          label={plan ? "Find and prove it again" : "Find and prove isolation"}
          disabled={running || !data.can_run}
          onStarted={set}
        />
        {data.can_undo && !running ? (
          <Button variant="ghost" disabled={undoing} onClick={() => void undo()}>
            Undo
          </Button>
        ) : null}
      </div>
      <p className="m-0 text-[12px] text-ink-3">
        {data.can_run ? ISOLATE_COST : "Say where the app's code is first: it's found from it."}{" "}
        <HowIsolationLink />
      </p>
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
    </div>
  );
}
