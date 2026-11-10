import { Check, RotateCw } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import { ago } from "../../lib/format";
import type { Isolation, StoreWords, TestSetup } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { Spinner } from "../../ui/loading";
import { Card, CardHead, Chip, type ChipTone, Screen, ScreenHead } from "../connect/kit";
import { Fold } from "../shared/group";
import { HowIsolationLink } from "../shared/HowIsolationWorks";
import { ISOLATE_COST } from "../shared/IsolateButton";
import { BuildRecipeCard } from "./BuildRecipe";
import { Checklist } from "./Checklist";
import { CopyPicture, type ShownBlock, type Tone } from "./CopyPicture";
import { OutboundCard, OutboundRow } from "./Outbound";
import { PageReach } from "./PageReach";
import { PlanDetails } from "./PlanDetails";
import { PutBackTable } from "./PutBack";
import { SetupPanel } from "./SetupPanel";
import { type IsolationState, useIsolation } from "./use-isolation";
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

/** Where the copy stands, in a chip. */
export function standingChip(
  at: Standing,
  version: number | null,
): { text: string; tone: ChipTone } {
  switch (at) {
    case "proven":
      return { text: version === null ? "Proven" : `Proven · plan v${version}`, tone: "ok" };
    case "stale":
      return { text: "Out of date", tone: "warn" };
    case "leaky":
      return { text: "Not isolated", tone: "no" };
    case "running":
      return { text: "Setting up", tone: "tint" };
    case "broke":
      return { text: "Setup stopped", tone: "no" };
    default:
      return { text: "Not set up", tone: "plain" };
  }
}

/**
 * The test environment: your app, never touched, beside the copy tests run on, built live while
 * the agent sets it up (the app on its own ports, its traces, one new block per store, the test
 * person), what leaves it, the steps with their times, and the setup a person can change; then
 * how tests reach the app and how another version is built. `setup` is the step of connecting an
 * app (`embedded` inside the live setup, whose feed says the steps), `page` its own screen.
 */
export function TestEnvironment({
  target,
  mode,
  onFirstTest,
  onStanding,
  embedded = false,
  state,
}: {
  target: string;
  mode: "setup" | "page";
  onFirstTest?: () => void;
  /** Where it stands, each time that changes. */
  onStanding?: (standing: Standing) => void;
  /** Inside the live setup: the steps are in its feed, the rest folded. */
  embedded?: boolean;
  /** The isolation read by the screen around it; else it reads its own. */
  state?: IsolationState;
}) {
  const own = useIsolation(state ? null : target);
  const { data, failed, set } = state ?? own;
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
    const waiting = failed ? (
      <p className="m-0 text-ink-2">Couldn't load the test environment.</p>
    ) : (
      <Skeleton className="h-72 rounded-2xl" />
    );
    return mode === "page" ? (
      <>
        <ScreenHead title="Test environment" />
        {waiting}
      </>
    ) : (
      waiting
    );
  }
  const now = at ?? "none";
  const stores = ready(health)?.isolation?.stores ?? [];
  const job = data.job;
  const live = now === "running" || now === "broke" || (mode === "setup" && job !== null);
  const blocks = pictureBlocks(data, live, stores, setup);
  const steps = job?.steps.length ? job.steps : waitingSteps(target, stores);
  const checklist = !embedded && (live || now === "none");
  const yours = [...new Set(stores.map((store) => yourKind(store.kind, store.holds)))];
  const settled = now !== "running" && now !== "broke";

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
  const startButton = (label: string, icon?: ReactNode) => (
    <Button
      size="lg"
      variant={mode === "setup" || now === "none" ? "default" : "secondary"}
      disabled={starting || !data.can_run}
      title={data.can_run ? ISOLATE_COST : NO_CODE}
      onClick={() => void start()}
    >
      {starting ? <Spinner /> : icon}
      {label}
    </Button>
  );

  const hint =
    now === "running" ? "The agent is setting this up…" : data.can_run ? ISOLATE_COST : NO_CODE;
  let action: ReactNode = null;
  switch (now) {
    case "running":
      break;
    case "proven":
      if (mode === "page") action = startButton("Check again", <RotateCw />);
      break;
    case "broke":
    case "leaky":
      action = startButton("Try again");
      break;
    case "stale":
      action = startButton("Prove it again");
      break;
    default:
      action = startButton("Set up testing");
  }
  if (mode === "setup" && !embedded && (now === "running" || now === "proven")) {
    action = (
      <Button size="lg" disabled={now !== "proven"} onClick={onFirstTest}>
        Write your first test
      </Button>
    );
  }

  const notes = (
    <>
      {now === "stale" ? (
        <p className="m-0 mt-3 text-unjudged">
          A commit changed {data.stale_files.join(", ") || "a file it rests on"}. The next run
          proves it again first.
        </p>
      ) : null}
      {now === "leaky" ? (
        <p className="m-0 mt-3 text-fail">
          The last check found {target} keeping things it doesn't put back, so tries may see what
          earlier ones left.
        </p>
      ) : null}
      {data.problem ? <p className="m-0 mt-3 text-fail">{data.problem}</p> : null}
    </>
  );
  const proofChip =
    now === "proven" ? (
      <Chip tone="ok">
        <Check aria-hidden />
        Resets cleanly
      </Chip>
    ) : null;

  const picture = (
    <section aria-label="Test copy" className="contents">
      <Card>
        {embedded ? <CardHead title="Where tests will run">{proofChip}</CardHead> : null}
        <div className="p-5 max-md:p-4">
          <CopyPicture
            target={target}
            yours={yours}
            blocks={blocks}
            tone={TONES[now]}
            badge={
              mode === "setup" && settled && now !== "proven" ? (
                <Chip tone={standingChip(now, data.version).tone} className="h-6">
                  {standingChip(now, data.version).text}
                </Chip>
              ) : null
            }
            animate={live}
            empty={now === "running" ? "Starting…" : "Made when it's set up"}
          />
          {notes}
        </div>
        <div className="px-5 pb-5 max-md:px-4">
          <OutboundRow outbound={data.outbound} />
        </div>
        {checklist ? (
          <div className="border-t border-rule px-5 py-3.5 max-md:px-4">
            <Checklist steps={steps} />
            {now === "broke" && job?.error ? (
              <p className="m-0 mt-2 text-sm text-unjudged">{job.error}</p>
            ) : null}
          </div>
        ) : null}
        {mode === "setup" && (action || !embedded) ? (
          <div className="flex flex-wrap items-center gap-3 border-t border-rule px-5 py-3 max-md:px-4">
            <HowIsolationLink />
            <span className="min-w-0 flex-1 text-sm text-ink-3">{hint}</span>
            {action}
          </div>
        ) : null}
        {error ? <p className="m-0 px-5 pb-3 text-sm text-fail max-md:px-4">{error}</p> : null}
      </Card>
    </section>
  );

  const panel = setup ? (
    <SetupPanel target={target} setup={setup} locked={now === "running"} onChanged={setSetup} />
  ) : null;

  if (mode === "setup") {
    if (embedded) {
      return (
        <div className="flex min-w-0 flex-col gap-3" data-testid="test-environment">
          {picture}
          <Fold title="Test setup">
            <div className="flex flex-col gap-4">
              {panel}
              {settled ? <PageReach target={target} /> : null}
            </div>
          </Fold>
        </div>
      );
    }
    return (
      <div className="flex min-w-0 flex-col gap-4" data-testid="test-environment">
        <h3 className="m-0 text-[18px] font-semibold tracking-[-0.02em]">
          {now === "proven" ? `Ready to test ${target}` : `Setting up a place to test ${target}`}
        </h3>
        <div className="grid items-start gap-4">
          {picture}
          {panel}
        </div>
        {settled ? <PageReach target={target} /> : null}
      </div>
    );
  }

  const chip = standingChip(now, data.version);
  const checkedAt = data.plan?.updated ?? null;
  return (
    <div className="flex min-w-0 flex-col gap-5" data-testid="test-environment">
      <ScreenHead
        title="Test environment"
        chips={
          <>
            <Chip tone={chip.tone}>
              {now === "proven" ? <Check aria-hidden /> : now === "running" ? <Spinner /> : null}
              {chip.text}
            </Chip>
            {checkedAt && now !== "none" ? <Chip>Checked {ago(checkedAt)}</Chip> : null}
          </>
        }
        actions={
          <>
            <HowIsolationLink />
            {action}
          </>
        }
      />
      {error ? <p className="m-0 text-sm text-fail">{error}</p> : null}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-5">
          {picture}
          <PutBackTable isolation={data} stores={stores} />
          {settled ? <PageReach target={target} /> : null}
          {settled ? <BuildRecipeCard target={target} /> : null}
          {data.plan && settled ? (
            <Fold title="Details">
              <PlanDetails isolation={data} onChange={set} />
            </Fold>
          ) : null}
        </div>
        <div className="flex min-w-0 flex-col gap-5">
          {panel}
          <OutboundCard
            target={target}
            outbound={data.outbound}
            locked={now === "running"}
            onChange={(outbound) => set({ ...data, outbound })}
          />
          {settled && job?.steps.length ? (
            <Card aria-label="Last check">
              <div className="flex flex-col gap-3 p-5 max-md:p-4">
                <h2 className="m-0 text-[15px] font-semibold">Last check</h2>
                <Checklist steps={job.steps} />
              </div>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** The Test environment screen: the copy as it stands, its setup, and how tests reach it. */
export function TestEnvironmentView({ target }: { target: string }) {
  return (
    <Screen>
      <TestEnvironment target={target} mode="page" />
    </Screen>
  );
}
