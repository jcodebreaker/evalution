import {
  Check,
  Clock,
  Folder,
  GitBranch,
  GitFork,
  GitPullRequest,
  LoaderCircle,
  Lock,
  Plus,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { appApi } from "../../lib/api";
import { cn } from "../../lib/cn";
import type {
  AgentSession,
  DiscoveryView,
  IsolationView,
  LearnJob,
  Target,
  TargetDetail,
  UnderstandingView,
} from "../../lib/schema";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
import { CopyPicture } from "../environment/CopyPicture";
import { TestEnvironment } from "../environment/TestEnvironment";
import { UiOffer } from "../environment/UiOffer";
import { useIsolation } from "../environment/use-isolation";
import { type Standing, standing, stepTime } from "../environment/words";
import { DiagramView } from "../learned/Diagram";
import { useLearnJob } from "../learned/use-learn-job";
import { Composer } from "../tests/Composer";
import { FocusPick } from "./FocusPick";
import { Card, CardHead, Chip, Screen, ScreenHead } from "./kit";
import { ModelStep } from "./ModelStep";
import { Probe } from "./Probe";
import { lastPart } from "./recent-folders";
import { TracesHelp } from "./TracesHelp";

export type StageState = "waiting" | "doing" | "done" | "problem";

/** One of the four stages: Code, Talks, Test copy, Map. */
export type Stage = { title: string; state: StageState; sub: string; fill: number };

/** One line of "What it's doing". `blocked`: a setting kept from leaving the copy. */
export type FeedLine = {
  key: string;
  text: string;
  state: "doing" | "done" | "problem" | "blocked";
  meta?: string;
};

const LINE: Record<"doing" | "done" | "problem", FeedLine["state"]> = {
  doing: "doing",
  done: "done",
  problem: "problem",
};

/** What the setup is doing, line by line, from the jobs it follows: the discovery's steps, the
 * connection, the test message, the copy's steps and what leaves it, then the learn's areas. */
export function feedLines({
  job,
  made,
  checked,
  isolation,
  understanding,
  writer,
  learned,
  learning = null,
}: {
  job: DiscoveryView | null;
  made: TargetDetail | null;
  checked: boolean;
  isolation: IsolationView | null;
  understanding: UnderstandingView | null;
  writer: AgentSession | null;
  learned: boolean;
  /** The app's learn job while it runs (C10): what the map lines follow. */
  learning?: LearnJob | null;
}): FeedLine[] {
  const lines: FeedLine[] = (job?.steps ?? []).map((step, index) => ({
    key: `find:${index}`,
    text: step.text,
    state: LINE[step.state],
  }));
  if (made) lines.push({ key: "made", text: `Connected as ${made.adapter_label}`, state: "done" });
  if (checked) lines.push({ key: "talks", text: "Sent a message, got a reply", state: "done" });
  for (const step of isolation?.job?.steps ?? []) {
    if (step.state === "waiting") continue;
    lines.push({
      key: `copy:${step.key}`,
      text: step.state === "failed" && step.detail ? `${step.label}: ${step.detail}` : step.label,
      state: step.state === "running" ? "doing" : step.state === "failed" ? "problem" : "done",
      meta: step.seconds === null ? undefined : stepTime(step.seconds),
    });
  }
  const blocked = (isolation?.outbound ?? []).filter((entry) => !entry.allowed);
  if (blocked.length && (isolation?.job || isolation?.status === "proven")) {
    lines.push({
      key: "outbound",
      text: `Blanked ${blocked.length} live ${blocked.length === 1 ? "key" : "keys"} in the copy`,
      state: "blocked",
      meta: blocked.map((entry) => entry.name).join(", "),
    });
  }
  const progress = understanding?.progress;
  const live = (learning?.state === "running" ? learning.progress : null) ?? writer?.learn ?? null;
  if (live && !progress?.areas.length) {
    lines.push({
      key: "learn:skeleton",
      text: live.stage === "map" ? "Mapping the code" : "Finding prompts, tools and state",
      state: live.stage === "map" || live.stage === "skeleton" ? "doing" : "done",
      meta: live.files ? `${live.files} files` : undefined,
    });
  } else if (!live && writer?.status === "working" && writer.activity.length) {
    lines.push({
      key: "learn:now",
      text: writer.activity.at(-1)?.text ?? "Learning",
      state: "doing",
    });
  }
  for (const area of progress?.areas ?? []) {
    const name = area.title || area.id;
    if (area.state === "learning")
      lines.push({ key: `area:${area.id}`, text: `Reading ${name}`, state: "doing" });
    else if (area.state === "done" || area.state === "current")
      lines.push({
        key: `area:${area.id}`,
        text: `Read ${name}`,
        state: "done",
        meta: `${area.files} files`,
      });
    else if (area.state === "failed")
      lines.push({
        key: `area:${area.id}`,
        text: `Couldn't read ${name}`,
        state: "problem",
        meta: area.reason ?? undefined,
      });
  }
  if (learned && understanding) {
    const n = (understanding.progress?.areas ?? []).filter(
      (area) => area.state !== "outside",
    ).length;
    lines.push({
      key: "learned",
      text: `Mapped ${understanding.card.name}`,
      state: "done",
      meta: n ? `${n} areas` : undefined,
    });
  }
  return lines;
}

const COPY: Record<Standing, { state: StageState; sub: string; fill: number }> = {
  none: { state: "waiting", sub: "not set up", fill: 0 },
  running: { state: "doing", sub: "copying", fill: 40 },
  proven: { state: "done", sub: "resets cleanly", fill: 100 },
  stale: { state: "problem", sub: "out of date", fill: 100 },
  leaky: { state: "problem", sub: "not isolated", fill: 100 },
  broke: { state: "problem", sub: "stopped", fill: 100 },
};

/** The four stages, from the same jobs as the feed. */
export function stages({
  job,
  made,
  checked,
  isolation,
  understanding,
  modelChosen,
  learning,
  learned,
}: {
  job: DiscoveryView | null;
  made: TargetDetail | null;
  checked: boolean;
  isolation: IsolationView | null;
  understanding: UnderstandingView | null;
  modelChosen: boolean;
  learning: boolean;
  learned: boolean;
}): Stage[] {
  const steps = job?.steps ?? [];
  const code: Stage = made
    ? { title: "Code", state: "done", sub: made.adapter_label || "connected", fill: 100 }
    : job?.status === "failed"
      ? { title: "Code", state: "problem", sub: "stopped", fill: 100 }
      : job?.status === "ready"
        ? { title: "Code", state: "doing", sub: "needs you", fill: 80 }
        : {
            title: "Code",
            state: "doing",
            sub: "reading",
            fill: Math.min(90, 15 + steps.filter((step) => step.state === "done").length * 15),
          };
  const talks: Stage = !made
    ? { title: "Talks", state: "waiting", sub: "waiting", fill: 0 }
    : checked
      ? { title: "Talks", state: "done", sub: "replies", fill: 100 }
      : { title: "Talks", state: "doing", sub: "test message", fill: 40 };
  const at = isolation ? standing(isolation) : "none";
  const copySteps = isolation?.job?.steps ?? [];
  const copy: Stage = !made
    ? { title: "Test copy", state: "waiting", sub: "waiting", fill: 0 }
    : {
        title: "Test copy",
        ...COPY[at],
        ...(at === "running" && copySteps.length
          ? {
              sub: `${copySteps.filter((step) => step.state === "done").length} of ${copySteps.length}`,
              fill: Math.max(
                10,
                (100 * copySteps.filter((step) => step.state === "done").length) / copySteps.length,
              ),
            }
          : {}),
        ...(at === "none" ? { state: "doing" as const, sub: "needs you" } : {}),
      };
  const areas = (understanding?.progress?.areas ?? []).filter((area) => area.state !== "outside");
  const done = areas.filter((area) => area.state === "done" || area.state === "current").length;
  const map: Stage = !modelChosen
    ? { title: "Map", state: "waiting", sub: "needs a model", fill: 0 }
    : learned
      ? {
          title: "Map",
          state: "done",
          sub: areas.length ? `${areas.length} areas` : "learned",
          fill: 100,
        }
      : learning || made
        ? {
            title: "Map",
            state: "doing",
            sub: areas.length ? `${done} of ${areas.length}` : "starting",
            fill: areas.length ? Math.max(5, (100 * done) / areas.length) : 5,
          }
        : { title: "Map", state: "waiting", sub: "waiting", fill: 0 };
  return [code, talks, copy, map];
}

function StageMark({ state }: { state: StageState }) {
  return (
    <span
      className={cn(
        "grid size-9 shrink-0 place-items-center rounded-full [&_svg]:size-[18px]",
        state === "done" && "bg-pass-soft text-pass",
        state === "doing" && "bg-tint-soft text-tint-ink",
        state === "problem" && "bg-fail-soft text-fail",
        state === "waiting" && "bg-sunk text-ink-3",
      )}
    >
      {state === "done" ? (
        <Check aria-hidden />
      ) : state === "doing" ? (
        <LoaderCircle aria-hidden className="animate-spin" />
      ) : state === "problem" ? (
        <TriangleAlert aria-hidden />
      ) : (
        <Clock aria-hidden />
      )}
    </span>
  );
}

function Stages({ stages: shown }: { stages: readonly Stage[] }) {
  return (
    <Card>
      <ol className="m-0 grid list-none grid-cols-4 p-0 max-md:grid-cols-2" aria-label="Stages">
        {shown.map((stage) => (
          <li
            key={stage.title}
            data-state={stage.state}
            aria-label={stage.title}
            className="relative flex min-w-0 items-center gap-3 border-rule px-5 py-4 not-last:border-e max-md:px-4"
          >
            <StageMark state={stage.state} />
            <span className="min-w-0">
              <b className="block leading-tight font-semibold">{stage.title}</b>
              <small className="block truncate font-mono text-[12px] text-ink-3">{stage.sub}</small>
            </span>
            <i
              aria-hidden
              className={cn(
                "absolute bottom-0 left-0 h-[3px] transition-[width] duration-500",
                stage.state === "doing"
                  ? "bg-tint"
                  : stage.state === "problem"
                    ? "bg-fail"
                    : "bg-pass",
              )}
              style={{ width: `${stage.fill}%` }}
            />
          </li>
        ))}
      </ol>
    </Card>
  );
}

function Feed({ lines, live }: { lines: readonly FeedLine[]; live: boolean }) {
  const box = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const at = box.current;
    if (at && lines.length) at.scrollTop = at.scrollHeight;
  }, [lines.length]);
  return (
    <Card>
      <CardHead title="What it's doing">
        <span
          role="img"
          aria-label={live ? "Working" : "Done"}
          className={cn("size-2.5 rounded-full", live ? "animate-pulse bg-tint" : "bg-pass")}
        />
      </CardHead>
      <ol
        ref={box}
        aria-label="What it's doing"
        className="m-0 flex h-[384px] list-none flex-col overflow-auto p-0 py-2 max-md:h-auto max-md:max-h-[384px]"
      >
        {lines.map((line) => (
          <li
            key={line.key}
            data-state={line.state}
            className="block-in grid grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-3 px-5 py-2 max-md:px-4"
          >
            {line.state === "done" ? (
              <Check role="img" aria-label="Done" className="size-[15px] text-pass" />
            ) : line.state === "doing" ? (
              <LoaderCircle
                role="img"
                aria-label="Working"
                className="size-[15px] animate-spin text-tint"
              />
            ) : line.state === "blocked" ? (
              <Lock role="img" aria-label="Blocked" className="size-[15px] text-fail" />
            ) : (
              <TriangleAlert role="img" aria-label="Problem" className="size-[15px] text-fail" />
            )}
            <span className="truncate" title={line.text}>
              {line.text}
            </span>
            <span
              className="max-w-[16ch] truncate font-mono text-[13px] text-ink-3"
              title={line.meta}
            >
              {line.meta ?? ""}
            </span>
          </li>
        ))}
        {lines.length ? null : <li className="px-5 py-2 text-ink-3">Starting…</li>}
      </ol>
    </Card>
  );
}

function useClock(running: boolean) {
  const [began] = useState(() => Date.now());
  const [now, setNow] = useState(began);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const seconds = Math.max(0, Math.round((now - began) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * Setting an app up, live: four stages (Code, Talks, Test copy, Map) from the jobs it follows,
 * what it's doing line by line, the copy drawn as it is made with what leaves it, and the map
 * filling in as the areas land; then the tests worth writing first. What needs the person (a
 * question, the traces, the model, the test message) shows where it's asked.
 */
export function SetupLive({
  job,
  made,
  row,
  busy,
  error,
  checking,
  answers,
  onAnswers,
  onAnswer,
  onAccept,
  onCheckTraces,
  onReloadTargets,
  onAgain,
  modelChosen,
  onModelChosen,
  writer,
  learning,
  learned,
  details,
  onLearned,
  onFirstTest,
  onAsk,
}: {
  job: DiscoveryView | null;
  made: TargetDetail | null;
  row: Target | null;
  busy: boolean;
  error: string | null;
  checking: boolean;
  answers: Record<string, string>;
  onAnswers: (answers: Record<string, string>) => void;
  onAnswer: () => void;
  onAccept: () => void;
  onCheckTraces: () => void;
  onReloadTargets: () => Promise<unknown>;
  /** Back to the field, after a discovery that stopped. */
  onAgain: () => void;
  modelChosen: boolean;
  onModelChosen: (chosen: boolean) => void;
  writer: AgentSession | null;
  learning: boolean;
  learned: boolean;
  details: ReactNode;
  onLearned: () => void;
  onFirstTest?: () => void;
  onAsk: (text: string) => Promise<void>;
}) {
  const name = made?.name ?? null;
  const shownName = name ?? job?.target?.name ?? lastPart(job?.folder ?? "your app");
  const iso = useIsolation(name);
  const [understood, reload] = useLoad(
    name && modelChosen ? `setup-understanding:${name}` : null,
    () => appApi.understanding(name ?? ""),
  );
  const understanding = ready(understood);
  // The app's learn is a job of its own (C10): the Map stage follows it, polled until it ends.
  const learnJob = useLearnJob(name && modelChosen ? name : null);
  const jobNow = learnJob.job;
  useVisiblePoll(
    learnJob.reload,
    POLL_MS,
    Boolean(name && modelChosen) && (!jobNow || jobNow.state === "running"),
  );
  const learningNow = learning || learnJob.running;
  const learnedNow = learned || (jobNow?.state === "done" && !learningNow);
  const mapping = learningNow || understanding?.progress?.state === "learning";
  useVisiblePoll(reload, POLL_MS, mapping);
  const wasLearning = useRef(learningNow);
  useEffect(() => {
    if (wasLearning.current && !learningNow) void reload();
    wasLearning.current = learningNow;
  }, [learningNow, reload]);

  const checked = !!row?.checked;
  const isolation = iso.data;
  const proven = isolation?.status === "proven" && isolation.job?.state !== "running";
  const done = learnedNow && proven;
  const lines = feedLines({
    job,
    made,
    checked,
    isolation,
    understanding,
    writer,
    learned: learnedNow,
    learning: jobNow,
  });
  const shown = stages({
    job,
    made,
    checked,
    isolation,
    understanding,
    modelChosen,
    learning: mapping,
    learned: learnedNow,
  });
  const clock = useClock(!done && job?.status !== "failed");

  const asking = job?.status === "ready" && job.questions.length > 0 && !made;
  // A question answered from a pick list (which agent to test) holds "All of them" unpicked.
  const answered =
    job?.questions.every((q) => q.choices?.length || answers[q.key]?.trim()) ?? false;
  const waiting = !!job?.traces && !job.traces.arriving && job.status === "ready" && !made;
  const stopped = job?.status === "failed" && !made;
  const repository = job?.repository ?? null;

  const areas = (understanding?.progress?.areas ?? []).filter((area) => area.state !== "outside");
  const areasDone = areas.filter(
    (area) => area.state === "done" || area.state === "current",
  ).length;
  const starters = (understanding?.starters ?? []).filter((entry) => !entry.exists).slice(0, 3);

  return (
    <Screen>
      <ScreenHead
        title={done ? `${shownName} is ready` : `Setting up ${shownName}`}
        chips={
          <>
            {repository ? (
              <>
                <Chip mono title={repository.said || undefined}>
                  <GitFork aria-hidden />
                  {repository.owner}/{repository.repo}
                </Chip>
                {repository.pull !== null ? (
                  <Chip mono>
                    <GitPullRequest aria-hidden />#{repository.pull}
                  </Chip>
                ) : repository.ref ? (
                  <Chip mono>
                    <GitBranch aria-hidden />
                    {repository.ref}
                  </Chip>
                ) : null}
              </>
            ) : job ? (
              <Chip mono title={job.folder}>
                <Folder aria-hidden />
                <span className="truncate">{job.folder}</span>
              </Chip>
            ) : null}
            <Chip mono aria-label="Time so far">
              {clock}
            </Chip>
          </>
        }
        actions={
          <>
            {made && modelChosen ? (
              <Button variant="secondary" size="lg" onClick={onLearned}>
                What it learned
              </Button>
            ) : null}
            <Button size="lg" disabled={!done} onClick={onFirstTest}>
              <Sparkles />
              Write the first test
            </Button>
          </>
        }
      />
      <Stages stages={shown} />
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-5">
          <Feed lines={lines} live={!done && !stopped} />
          {stopped || asking || waiting || error ? (
            <Card aria-label="Needs you">
              <CardHead title="Needs you" />
              <div className="flex flex-col gap-3 p-5 max-md:p-4">
                {stopped ? (
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="min-w-0 flex-1 text-ink-2">
                      {[...(job?.steps ?? [])].reverse().find((step) => step.state === "problem")
                        ?.text ?? "It couldn't read this one."}
                    </span>
                    <Button variant="secondary" onClick={onAgain}>
                      Try another
                    </Button>
                  </div>
                ) : null}
                {asking && job ? (
                  <form
                    className="flex flex-col gap-3"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (answered) onAnswer();
                    }}
                  >
                    {job.questions.map((question) =>
                      question.choices?.length ? (
                        <FocusPick
                          key={question.key}
                          label={question.text}
                          choices={question.choices}
                          value={answers[question.key]}
                          disabled={busy}
                          onPick={(value) => onAnswers({ ...answers, [question.key]: value })}
                        />
                      ) : (
                        <div key={question.key} className="flex flex-col gap-1.5 text-sm">
                          <label htmlFor={`question-${question.key}`}>{question.text}</label>
                          <Input
                            id={`question-${question.key}`}
                            value={answers[question.key] ?? ""}
                            onChange={(event) =>
                              onAnswers({ ...answers, [question.key]: event.target.value })
                            }
                            placeholder={question.hint}
                            autoComplete="off"
                          />
                        </div>
                      ),
                    )}
                    <Button type="submit" className="self-end" disabled={!answered || busy}>
                      Connect
                    </Button>
                  </form>
                ) : null}
                {waiting && job?.traces ? (
                  <>
                    <TracesHelp traces={job.traces} checking={checking} onCheck={onCheckTraces} />
                    {asking ? null : (
                      <Button className="self-end" disabled={busy} onClick={onAccept}>
                        Connect
                      </Button>
                    )}
                  </>
                ) : null}
                {error ? (
                  <p role="alert" className="m-0 text-sm text-fail">
                    {error}
                  </p>
                ) : null}
              </div>
            </Card>
          ) : null}
          {name ? (
            <Card aria-label="Talks">
              <CardHead title="Talks" />
              <div className="flex flex-col gap-3 p-5 max-md:p-4">
                <Probe target={name} checked={checked} onChange={onReloadTargets} />
              </div>
            </Card>
          ) : null}
          <Card aria-label="Model">
            <CardHead title="Model" />
            <div className="flex flex-col gap-3 p-5 max-md:p-4">
              <ModelStep onChosen={onModelChosen} />
            </div>
          </Card>
        </div>
        <div className="flex min-w-0 flex-col gap-5">
          {name ? (
            <TestEnvironment target={name} mode="setup" embedded state={iso} />
          ) : (
            <Card>
              <CardHead title="Where tests will run" />
              <div className="p-5 max-md:p-4">
                <CopyPicture
                  target={shownName}
                  yours={[]}
                  blocks={[]}
                  tone="plain"
                  animate={false}
                  empty="Made once it's connected"
                />
              </div>
            </Card>
          )}
          <Card aria-label="What it's learning">
            <CardHead title="What it's learning">
              {areas.length ? (
                <span className="font-mono text-[13px] text-ink-3">
                  {areasDone} of {areas.length} areas
                </span>
              ) : null}
            </CardHead>
            <div className="p-5 max-md:p-4">
              {understanding?.diagram.nodes.length ? (
                <DiagramView
                  diagram={understanding.diagram}
                  name={understanding.card.name}
                  selected={null}
                  onSelect={onLearned}
                />
              ) : (
                <div
                  className={cn(
                    "grid h-[200px] place-items-center rounded-[14px] bg-sunk text-sm text-ink-3",
                    mapping && "animate-pulse",
                  )}
                >
                  {modelChosen ? (mapping ? "Fills in as it learns" : "Waiting") : "Needs a model"}
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
      {name && modelChosen ? (
        <Card aria-label="First test">
          <CardHead
            title={
              <span className="flex items-center gap-2.5">
                {done ? (
                  <span className="grid size-9 place-items-center rounded-[10px] bg-pass-soft text-pass">
                    <Check aria-hidden className="size-[18px]" />
                  </span>
                ) : null}
                {done ? `${shownName} is ready` : "First test"}
              </span>
            }
          />
          <div className="flex flex-col gap-5 p-5 max-md:p-4">
            {starters.length ? (
              <div className="grid gap-3.5 md:grid-cols-3">
                {starters.map((entry) => (
                  <div
                    key={entry.id}
                    className="flex flex-col gap-2.5 rounded-2xl border border-rule p-5 max-md:p-4"
                  >
                    <b className="font-medium">{entry.title}</b>
                    {entry.what ? <span className="text-sm text-ink-3">{entry.what}</span> : null}
                    <Button
                      variant="secondary"
                      size="sm"
                      className="self-start"
                      onClick={() =>
                        void onAsk(
                          `Write the suggested test "${entry.title}"${entry.what ? `: ${entry.what}` : ""}`,
                        )
                      }
                    >
                      <Plus />
                      Add
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}
            <UiOffer
              // Read again once the copy is proven: the agent can learn the page from then.
              key={proven ? "proven" : "not yet"}
              target={name}
              className="rounded-2xl border border-rule bg-group p-5 max-md:p-4"
            />
            <Composer target={name} onAsk={onAsk} />
          </div>
        </Card>
      ) : null}
      {details}
    </Screen>
  );
}
