import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { Isolation, StoreWords, TestSetup } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { Spinner } from "../../ui/loading";
import { Fold } from "../shared/group";
import { HowIsolationLink } from "../shared/HowIsolationWorks";
import { ISOLATE_COST } from "../shared/IsolateButton";
import { Page, Toolbar, ToolbarTitle } from "../shared/layout";
import { Checklist } from "./Checklist";
import { CopyPicture, type ShownBlock, type Tone } from "./CopyPicture";
import { PlanDetails } from "./PlanDetails";
import { SetupPanel } from "./SetupPanel";
import { useIsolation } from "./use-isolation";
import {
  type Block,
  begun,
  personBlock,
  type Standing,
  standing,
  storeBlock,
  waitingSteps,
  yourKind,
} from "./words";

const NO_CODE = "Say where the app's code is first: it's found from it.";
const LEDE = "Your app stays as it is. Tests run on a copy that starts fresh every time.";

/** The copy's own blocks, before its stores: the app on its own ports, and its traces. */
const appBlocks = (target: string): Block[] => [
  { key: "app", kind: "app", title: target, sub: "own ports" },
  { key: "traces", kind: "traces", title: "Traces", sub: "to evalution" },
];

/** What the picture shows: live from the job's steps while it runs, else from the saved plan. */
function pictureBlocks(
  isolation: Isolation,
  live: boolean,
  stores: readonly StoreWords[],
  setup: TestSetup | null,
): ShownBlock[] {
  const target = isolation.target;
  const start = setup?.start.kind ?? "empty";
  const planKinds = new Map((isolation.plan?.stores ?? []).map((store) => [store.id, store.kind]));
  const proven = isolation.status === "proven";
  if (live && isolation.job) {
    const steps = isolation.job.steps;
    const step = (key: string) => steps.find((entry) => entry.key === key);
    const prove = step("prove");
    const checked = prove?.state === "done" && proven;
    const marking = prove?.state === "running";
    const blocks: Block[] = [];
    if (begun(step("copy"))) blocks.push(...appBlocks(target));
    for (const entry of steps) {
      if (entry.key.startsWith("store:") && begun(entry)) {
        blocks.push(storeBlock(entry.key.slice(6), stores, planKinds, start, entry.label));
      }
    }
    if (begun(step("person"))) blocks.push(personBlock(setup, target));
    return blocks.map((block) => ({
      ...block,
      checked,
      marking: marking && block.kind === "store",
    }));
  }
  if (!isolation.plan || isolation.status === "none") return [];
  const kept = stores.filter((store) => store.reset).map((store) => store.id);
  const ids = kept.length
    ? kept
    : isolation.plan.stores
        .filter((store) => store.strategy === "relocate" || store.strategy === "snapshot")
        .map((store) => store.id);
  return [
    ...appBlocks(target),
    ...ids.map((id) => storeBlock(id, stores, planKinds, start, "")),
    personBlock(setup, target),
  ].map((block) => ({ ...block, checked: proven, marking: false }));
}

const TONES: Record<Standing, Tone> = {
  running: "plain",
  broke: "plain",
  none: "plain",
  proven: "ready",
  stale: "stale",
  leaky: "leaky",
};

function Badge({ standing: at }: { standing: Standing }) {
  const words =
    at === "stale"
      ? { text: "Out of date", tone: "bg-warning/10 text-warning" }
      : at === "leaky"
        ? { text: "Not isolated", tone: "bg-destructive/10 text-destructive" }
        : at === "none"
          ? { text: "Not set up", tone: "bg-bubble text-ink-2" }
          : null;
  if (!words) return null;
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium tracking-normal normal-case",
        words.tone,
      )}
    >
      {words.text}
    </span>
  );
}

/**
 * The test environment: your app, never touched, beside the copy tests run on, built live while
 * the agent sets it up (the app on its own ports, its traces, one new block per store, the test
 * person), the steps with their times, and the setup a person can change. `setup` is the step of
 * connecting an app, ending in its first test; `page` is the screen it settles into.
 */
export function TestEnvironment({
  target,
  mode,
  onFirstTest,
  onStanding,
}: {
  target: string;
  mode: "setup" | "page";
  onFirstTest?: () => void;
  /** Where it stands, each time that changes. */
  onStanding?: (standing: Standing) => void;
}) {
  const { data, failed, set } = useIsolation(target);
  const [health, reloadHealth] = useLoad(`environment:${target}`, () => api.health(target, "7d"));
  const [setup, setSetup] = useState<TestSetup | null>(null);
  const readSetup = useCallback(() => {
    api.testSetup(target).then(setSetup, () => setSetup(null));
  }, [target]);
  useEffect(readSetup, [readSetup]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const at = data ? standing(data) : null;
  // When a run of the job ends, what it found names the stores, and the setup is proven again.
  const was = useRef(at);
  useEffect(() => {
    if (was.current === "running" && at !== "running") {
      void reloadHealth();
      readSetup();
    }
    was.current = at;
  }, [at, reloadHealth, readSetup]);
  const told = useRef(onStanding);
  told.current = onStanding;
  useEffect(() => {
    if (at) told.current?.(at);
  }, [at]);

  if (!data) {
    return failed ? (
      <p className="m-0 text-[13px] text-ink-2">Couldn't load the test environment.</p>
    ) : (
      <Skeleton className="h-72 rounded-[14px]" />
    );
  }
  const now = at ?? "none";
  const stores = ready(health)?.isolation?.stores ?? [];
  const job = data.job;
  const live = now === "running" || now === "broke" || (mode === "setup" && job !== null);
  const blocks = pictureBlocks(data, live, stores, setup);
  const steps = job?.steps.length ? job.steps : waitingSteps(target, stores);
  const checklist = live || now === "none";
  const yours = [...new Set(stores.map((store) => yourKind(store.kind, store.holds)))];

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      set(await api.runIsolation(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't start. Nothing was sent."));
    }
    setStarting(false);
  };
  const startButton = (label: string) => (
    <Button
      variant={mode === "setup" || now === "none" ? "default" : "secondary"}
      disabled={starting || !data.can_run}
      onClick={() => void start()}
    >
      {starting ? <Spinner /> : null}
      {label}
    </Button>
  );

  let hint: string;
  let action: ReactNode = null;
  switch (now) {
    case "running":
      hint = "The agent is setting this up…";
      break;
    case "proven":
      hint = "Every try starts from here.";
      break;
    case "broke":
      hint = data.can_run ? ISOLATE_COST : NO_CODE;
      action = startButton("Try again");
      break;
    case "stale":
      hint = data.can_run ? ISOLATE_COST : NO_CODE;
      action = startButton("Prove it again");
      break;
    case "leaky":
      hint = data.can_run ? ISOLATE_COST : NO_CODE;
      action = startButton("Try again");
      break;
    default:
      hint = data.can_run ? ISOLATE_COST : NO_CODE;
      action = startButton("Set up testing");
  }
  if (mode === "setup" && (now === "running" || now === "proven")) {
    action = (
      <Button disabled={now !== "proven"} onClick={onFirstTest}>
        Write your first test
      </Button>
    );
  }

  const settled = now !== "running" && now !== "broke";
  return (
    <div className="flex min-w-0 flex-col gap-4 text-[13px]" data-testid="test-environment">
      <div className="flex flex-col gap-1">
        {mode === "setup" ? (
          <h3 className="m-0 text-[18px] font-semibold tracking-[-0.02em]">
            {now === "proven" ? `Ready to test ${target}` : `Setting up a place to test ${target}`}
          </h3>
        ) : null}
        <p className="m-0 text-[14px] text-ink-2">{LEDE}</p>
      </div>
      <div
        className={cn(
          "grid items-start gap-4",
          mode === "page" && "xl:grid-cols-[minmax(0,1fr)_300px]",
        )}
      >
        <section
          className="flex min-w-0 flex-col rounded-[14px] border border-border bg-group"
          aria-label="Test copy"
        >
          <div className="p-5 max-md:p-4">
            <CopyPicture
              target={target}
              yours={yours}
              blocks={blocks}
              tone={TONES[now]}
              badge={settled ? <Badge standing={now} /> : null}
              animate={live}
              empty={now === "running" ? "Starting…" : "Made when it's set up"}
            />
            {now === "stale" ? (
              <p className="m-0 mt-3 text-warning">
                A commit changed {data.stale_files.join(", ") || "a file it rests on"}. The next run
                proves it again first.
              </p>
            ) : null}
            {now === "leaky" ? (
              <p className="m-0 mt-3 text-destructive">
                The last check found {target} keeping things it doesn't put back, so tries may see
                what earlier ones left.
              </p>
            ) : null}
            {data.problem ? <p className="m-0 mt-3 text-destructive">{data.problem}</p> : null}
          </div>
          {checklist ? (
            <div className="border-t border-separator px-5 py-3.5 max-md:px-4">
              <Checklist steps={steps} />
              {now === "broke" && job?.error ? (
                <p className="m-0 mt-2 text-[12.5px] text-warning">{job.error}</p>
              ) : null}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-3 border-t border-separator px-5 py-3 max-md:px-4">
            <HowIsolationLink />
            <span className="min-w-0 flex-1 text-[12px] text-ink-3">{hint}</span>
            {action}
          </div>
          {error ? (
            <p className="m-0 px-5 pb-3 text-[12.5px] text-destructive max-md:px-4">{error}</p>
          ) : null}
        </section>
        {setup ? (
          <SetupPanel
            target={target}
            setup={setup}
            locked={now === "running"}
            onChanged={setSetup}
          />
        ) : null}
      </div>
      {mode === "page" && data.plan && settled ? (
        <Fold title="Details">
          <PlanDetails isolation={data} onChange={set} />
        </Fold>
      ) : null}
    </div>
  );
}

/** The Test environment screen: the copy as it stands, and its setup. */
export function TestEnvironmentView({ target }: { target: string }) {
  return (
    <>
      <Toolbar>
        <ToolbarTitle title="Test environment" />
      </Toolbar>
      <Page wide>
        <TestEnvironment target={target} mode="page" />
      </Page>
    </>
  );
}
