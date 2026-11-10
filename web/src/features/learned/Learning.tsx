import { useEffect, useState } from "react";
import { cn } from "../../lib/cn";
import type { AgentSession, LearnArea, LearnProgress } from "../../lib/schema";
import { LoadingState, Spinner } from "../../ui/loading";
import { Group } from "../shared/group";
import { LearnAreaButton, type LearnAreaFn } from "./LearnArea";

/** Calls an area's agent may make at the least (the server's STEPS), when the progress doesn't
 * say: one bar segment each. */
const STEPS = 4;
/** The learn's stages as the card shows them, and the server's stages each stands for: the code
 * map is the skeleton's first part, and the fallback's reading is the areas' first. */
const STAGES = [
  ["skeleton", "Skeleton", ["map", "skeleton"]],
  ["areas", "Areas", ["read", "areas"]],
  ["overview", "Overview", ["overview"]],
] as const;

const STATE: Record<LearnArea["state"], { label: string; dot: string; pill: string; bar: string }> =
  {
    running: {
      label: "Running",
      dot: "bg-tint ring-3 ring-tint-soft-2",
      pill: "bg-tint-soft text-tint-ink",
      bar: "bg-tint",
    },
    learned: {
      label: "Done",
      dot: "bg-pass",
      pill: "bg-pass-soft text-pass",
      bar: "bg-pass",
    },
    failed: {
      label: "Failed",
      dot: "bg-fail",
      pill: "bg-fail-soft text-fail",
      bar: "bg-fail",
    },
    waiting: {
      label: "Waiting",
      dot: "ring-[1.5px] ring-inset ring-rule-strong",
      pill: "bg-sunk-2 text-ink-2",
      bar: "",
    },
    kept: { label: "Unchanged", dot: "bg-sunk-2", pill: "bg-sunk-2 text-ink-3", bar: "" },
    listed: {
      label: "Not learned",
      dot: "ring-[1.5px] ring-inset ring-rule",
      pill: "bg-sunk-2 text-ink-3",
      bar: "",
    },
    outside: {
      label: "Not part of the agent",
      dot: "ring-[1.5px] ring-inset ring-rule",
      pill: "bg-sunk-2 text-ink-3",
      bar: "",
    },
    later: {
      label: "Next learn",
      dot: "ring-[1.5px] ring-inset ring-rule-strong",
      pill: "bg-sunk-2 text-ink-2",
      bar: "",
    },
  };

const tokens = (n: number) => (n ? (n < 1000 ? `${n}` : `${Math.round(n / 1000)}k`) : "—");
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function useNow(on: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [on]);
  return now;
}

/** What the test writer is doing while it learns: its stages (Skeleton, Areas, Overview), then
 * each area's agent, the heaviest first. An area listed as weighing nothing offers "Learn this
 * area" (*onLearnArea*). Before the areas start (or a learn from traces alone) it is the last
 * steps, newest first. *areasShown*: the page lists the areas itself, so the card keeps to its
 * stages and time. */
export function Learning({
  session,
  onLearnArea,
  areasShown = false,
}: {
  session: AgentSession | null;
  onLearnArea?: LearnAreaFn;
  areasShown?: boolean;
}) {
  const progress = session?.learn ?? null;
  const now = useNow(Boolean(progress));
  if (progress) {
    return (
      <Areas progress={progress} now={now} onLearnArea={onLearnArea} areasShown={areasShown} />
    );
  }
  // A learn from the traces alone has no stages: its last step.
  return <LoadingState label={session?.activity.at(-1)?.text ?? "Learning your app…"} />;
}

function Areas({
  progress,
  now,
  onLearnArea,
  areasShown,
}: {
  progress: LearnProgress;
  now: number;
  onLearnArea?: LearnAreaFn;
  areasShown: boolean;
}) {
  const areas = progress.areas;
  const count = (state: LearnArea["state"]) => areas.filter((a) => a.state === state).length;
  const due = areas.filter(
    (a) =>
      a.state !== "kept" && a.state !== "listed" && a.state !== "outside" && a.state !== "later",
  );
  const finished = count("learned") + count("failed");
  const spent = areas.reduce((sum, a) => sum + a.tokens, 0);
  // "saving" is past the last stage: every stage shows done.
  const at = STAGES.findIndex(([, , stages]) =>
    (stages as readonly string[]).includes(progress.stage),
  );
  const meta: Record<string, string> = {
    skeleton:
      progress.stage === "map"
        ? progress.files
          ? `${progress.files} files`
          : ""
        : progress.skeleton != null
          ? `${progress.skeleton} ${progress.skeleton === 1 ? "part" : "parts"}`
          : "",
    areas: progress.stage === "read" ? "" : areas.length ? `${finished} of ${due.length}` : "",
    overview: progress.stage === "overview" ? "writing" : "",
  };
  return (
    <Group className="overflow-hidden" aria-label="Learning progress">
      <ol className="m-0 grid list-none grid-cols-3 border-separator border-b p-0">
        {STAGES.map(([key, name], index) => {
          const done = at < 0 || index < at;
          const current = index === at;
          return (
            <li
              key={key}
              className={cn(
                "flex flex-col gap-1 border-separator px-4 py-3 not-last:border-e",
                current && "bg-tint-soft/60",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <span
                  aria-hidden
                  className={cn(
                    "size-2 shrink-0 rounded-full",
                    done && "bg-pass",
                    current && "bg-tint ring-3 ring-tint-soft-2",
                    !done && !current && "ring-[1.5px] ring-inset ring-rule-strong",
                  )}
                />
                {name}
              </span>
              <span className="font-mono text-[13px] text-ink-3">
                {meta[key] || (done ? "done" : current ? "…" : "waiting")}
              </span>
            </li>
          );
        })}
      </ol>

      {areas.length ? null : (
        <div className="flex flex-col gap-2 px-4 py-3">
          <p className="m-0 flex items-center gap-2.5 text-sm text-ink-2">
            <Spinner />
            {progress.stage === "map"
              ? "Mapping the code"
              : progress.stage === "skeleton"
                ? "Finding the prompts, tools and state, and what real use touched"
                : progress.stage === "read" && progress.read
                  ? `Reading the files the code map can't parse · ${progress.read_done ?? 0} of ${progress.read}`
                  : "Splitting the code into areas"}
            <span className="ms-auto font-mono text-[13px] text-ink-3">
              {clock(now - progress.at)}
            </span>
          </p>
          {progress.stage === "read" && progress.read ? (
            <span className="h-1.5 overflow-hidden rounded-full bg-tint-soft" aria-hidden>
              <span
                className="block h-full rounded-full bg-tint transition-[width] duration-500"
                style={{ width: `${(100 * (progress.read_done ?? 0)) / progress.read}%` }}
              />
            </span>
          ) : null}
        </div>
      )}
      {areas.length && areasShown ? (
        <p className="m-0 flex flex-wrap gap-x-4 px-4 py-2.5 text-sm text-ink-3">
          {progress.parallel ? <span>{progress.parallel} areas at a time</span> : null}
          <span className="ms-auto font-mono">
            {tokens(spent)} · {clock(now - progress.at)}
          </span>
        </p>
      ) : null}
      {areasShown ? null : (
        <>
          <div
            hidden={!areas.length}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 border-separator border-b px-4 py-2.5 text-sm text-ink-2"
          >
            <span className="font-semibold text-foreground">
              {due.length} of {areas.length} areas
              {progress.parallel ? (
                <span className="font-normal text-ink-3"> · {progress.parallel} at a time</span>
              ) : null}
              {count("listed") ? (
                <span className="font-normal text-ink-3"> · {count("listed")} not learned</span>
              ) : null}
            </span>
            <span className="ms-auto flex flex-wrap gap-x-4">
              <span>
                <b className="font-semibold text-pass">{count("learned")}</b> done
              </span>
              <span>
                <b className="font-semibold text-tint">{count("running")}</b> running
              </span>
              <span>
                <b className="font-semibold text-foreground">{count("waiting")}</b> waiting
              </span>
              {count("failed") ? (
                <span>
                  <b className="font-semibold text-fail">{count("failed")}</b> failed
                </span>
              ) : null}
              <span className="font-mono text-ink-3">
                {tokens(spent)} · {clock(now - progress.at)}
              </span>
            </span>
          </div>

          <ul className="m-0 list-none p-0">
            {areas.map((area) => (
              <AreaRow key={area.id} area={area} now={now} onLearnArea={onLearnArea} />
            ))}
          </ul>
        </>
      )}
    </Group>
  );
}

function AreaRow({
  area,
  now,
  onLearnArea,
}: {
  area: LearnArea;
  now: number;
  onLearnArea?: LearnAreaFn;
}) {
  const look = STATE[area.state];
  const quiet = ["kept", "waiting", "listed", "outside", "later"].includes(area.state);
  const seconds =
    area.seconds ?? (area.state === "running" && area.since ? now - area.since : undefined);
  const steps = Math.max(area.steps ?? STEPS, 1);
  const filled = Math.min(area.calls, steps);
  const listed = area.state === "listed" || area.state === "outside";
  return (
    <li
      aria-label={area.id}
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_72px_44px_76px] items-center gap-x-3 border-separator px-4 py-2 not-last:border-b sm:grid-cols-[minmax(0,180px)_88px_minmax(0,1fr)_48px_44px_76px]",
        area.state === "running" && "bg-tint/[0.03]",
      )}
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <span aria-hidden className={cn("size-2 shrink-0 rounded-full", look.dot)} />
        <span className="flex min-w-0 flex-col">
          <span
            className={cn("truncate font-mono text-[13px] font-medium", quiet && "text-ink-3")}
            title={area.id}
          >
            {area.id}
          </span>
          <span className="text-sm text-ink-3">
            {area.files} {area.files === 1 ? "file" : "files"}
          </span>
        </span>
      </span>
      <span className="flex gap-[3px]" aria-hidden>
        {Array.from({ length: steps }, (_, step) => (
          <span
            key={step}
            className={cn(
              "h-1.5 min-w-0 flex-1 rounded-full",
              quiet && area.state !== "waiting"
                ? "bg-ink-3/20"
                : step < filled
                  ? look.bar
                  : "bg-tint-soft",
            )}
          />
        ))}
      </span>
      {listed && onLearnArea ? (
        <span className="col-span-2 flex justify-end sm:col-span-3">
          <LearnAreaButton area={area.id} onLearn={onLearnArea} />
        </span>
      ) : (
        <>
          <span
            className={cn(
              "hidden truncate font-mono text-[13px] sm:block",
              area.state === "running" ? "text-foreground" : "text-ink-3",
            )}
            title={area.now}
          >
            {area.now ||
              (area.state === "kept"
                ? "same files as last learn"
                : listed
                  ? "nothing in it the app uses"
                  : "")}
          </span>
          <span className="text-end font-mono text-[13px] text-ink-2">{tokens(area.tokens)}</span>
          <span className="hidden text-end font-mono text-[13px] text-ink-2 sm:block">
            {seconds != null ? `${Math.round(seconds)}s` : "—"}
          </span>
        </>
      )}
      <span
        className={cn(
          "justify-self-end rounded-full px-2 py-0.5 text-sm font-medium",
          look.pill,
          // On a phone the button takes the pill's place.
          listed && onLearnArea && "hidden sm:inline",
        )}
      >
        {look.label}
      </span>
    </li>
  );
}
