import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "../../lib/cn";
import type { AgentSession, LearnArea, LearnProgress } from "../../lib/schema";
import { LoadingState } from "../../ui/loading";
import { Group } from "../shared/group";

/** Calls an area's agent may make (the server's STEPS): one bar segment each. */
const STEPS = 4;
const STAGES = [
  ["map", "Map"],
  ["read", "Read"],
  ["areas", "Areas"],
  ["overview", "Overview"],
] as const;

const STATE: Record<LearnArea["state"], { label: string; dot: string; pill: string; bar: string }> =
  {
    running: {
      label: "Running",
      dot: "bg-tint ring-4 ring-tint/15",
      pill: "bg-tint/10 text-tint",
      bar: "bg-tint",
    },
    learned: {
      label: "Done",
      dot: "bg-success",
      pill: "bg-success/10 text-success",
      bar: "bg-success",
    },
    failed: {
      label: "Failed",
      dot: "bg-destructive",
      pill: "bg-destructive/10 text-destructive",
      bar: "bg-destructive",
    },
    waiting: { label: "Waiting", dot: "bg-ink-3/40", pill: "bg-selection text-ink-2", bar: "" },
    kept: { label: "Unchanged", dot: "bg-ink-3/25", pill: "bg-selection text-ink-3", bar: "" },
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

/** What the test writer is doing while it learns: its stages, then each area's agent. Before
 * the areas start (or a learn from traces alone) it is the last steps, newest first. */
export function Learning({ session }: { session: AgentSession | null }) {
  const progress = session?.learn ?? null;
  const now = useNow(Boolean(progress));
  if (!progress?.areas.length) {
    const activity = session?.activity ?? [];
    return (
      <div className="flex flex-col gap-1.5">
        <LoadingState label={stageLabel(progress) ?? "Learning your app…"} />
        {activity.length ? (
          <ol className="m-0 flex list-none flex-col gap-0.5 p-0 ps-[22px] text-[12px] text-ink-3">
            {activity
              .slice(-6)
              .reverse()
              .map((step) => (
                <li key={`${step.at}-${step.text}`}>{step.text}</li>
              ))}
          </ol>
        ) : null}
      </div>
    );
  }
  return <Areas progress={progress} now={now} />;
}

function stageLabel(progress: LearnProgress | null) {
  if (progress?.stage === "map") return "Mapping the code…";
  if (progress?.stage === "read") return `Reading ${progress.read ?? "the"} files…`;
  return null;
}

function Areas({ progress, now }: { progress: LearnProgress; now: number }) {
  const areas = progress.areas;
  const count = (state: LearnArea["state"]) => areas.filter((a) => a.state === state).length;
  const due = areas.filter((a) => a.state !== "kept");
  const finished = count("learned") + count("failed");
  const spent = areas.reduce((sum, a) => sum + a.tokens, 0);
  // "saving" is past the last stage: every stage shows done.
  const at = STAGES.findIndex(([key]) => key === progress.stage);
  const meta: Record<string, string> = {
    map: progress.files ? `${progress.files} files · ${progress.map_seconds ?? 0}s` : "",
    read: progress.read != null ? `${progress.read} files` : "",
    areas: `${finished} of ${due.length}`,
    overview: progress.stage === "overview" ? "writing" : "",
  };
  return (
    <Group className="overflow-hidden" aria-label="Learning progress">
      <ol className="m-0 grid list-none grid-cols-2 border-separator border-b p-0 sm:grid-cols-4">
        {STAGES.map(([key, name], index) => {
          const done = at < 0 || index < at;
          const current = index === at;
          return (
            <li
              key={key}
              className={cn(
                "flex flex-col gap-1 border-separator px-4 py-3 not-last:border-e",
                current && "bg-tint/5",
              )}
            >
              <span className="flex items-center gap-2 text-[13px] font-semibold">
                <span
                  aria-hidden
                  className={cn(
                    "flex size-4 items-center justify-center rounded-full",
                    done && "bg-success text-white",
                    current && "border-4 border-tint",
                    !done && !current && "border-[1.5px] border-ink-3/40",
                  )}
                >
                  {done ? <Check className="size-2.5" strokeWidth={3.5} /> : null}
                </span>
                {name}
              </span>
              <span className="font-mono text-[11.5px] text-ink-3">
                {meta[key] || (done ? "done" : current ? "…" : "waiting")}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-separator border-b px-4 py-2.5 text-[12px] text-ink-2">
        <span className="font-semibold text-foreground">
          {due.length} of {areas.length} areas
          {progress.parallel ? (
            <span className="font-normal text-ink-3"> · {progress.parallel} at a time</span>
          ) : null}
        </span>
        <span className="ms-auto flex flex-wrap gap-x-4">
          <span>
            <b className="font-semibold text-success">{count("learned")}</b> done
          </span>
          <span>
            <b className="font-semibold text-tint">{count("running")}</b> running
          </span>
          <span>
            <b className="font-semibold text-foreground">{count("waiting")}</b> waiting
          </span>
          {count("failed") ? (
            <span>
              <b className="font-semibold text-destructive">{count("failed")}</b> failed
            </span>
          ) : null}
          <span className="font-mono text-ink-3">
            {tokens(spent)} · {clock(now - progress.at)}
          </span>
        </span>
      </div>

      <ul className="m-0 list-none p-0">
        {areas.map((area) => (
          <AreaRow key={area.id} area={area} now={now} />
        ))}
      </ul>
    </Group>
  );
}

function AreaRow({ area, now }: { area: LearnArea; now: number }) {
  const look = STATE[area.state];
  const quiet = area.state === "kept" || area.state === "waiting";
  const seconds =
    area.seconds ?? (area.state === "running" && area.since ? now - area.since : undefined);
  const filled = Math.min(area.calls, STEPS);
  return (
    <li
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_72px_44px_76px] items-center gap-x-3 border-separator px-4 py-2 not-last:border-b sm:grid-cols-[minmax(0,180px)_88px_minmax(0,1fr)_48px_44px_76px]",
        area.state === "running" && "bg-tint/[0.03]",
      )}
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <span aria-hidden className={cn("size-2 shrink-0 rounded-full", look.dot)} />
        <span className="flex min-w-0 flex-col">
          <span
            className={cn("truncate font-mono text-[12.5px] font-medium", quiet && "text-ink-3")}
            title={area.id}
          >
            {area.id}
          </span>
          <span className="text-[11.5px] text-ink-3">
            {area.files} {area.files === 1 ? "file" : "files"}
          </span>
        </span>
      </span>
      <span className="grid grid-cols-4 gap-[3px]" aria-hidden>
        {[0, 1, 2, 3].map((step) => (
          <span
            key={step}
            className={cn(
              "h-1.5 rounded-full",
              area.state === "kept"
                ? "bg-ink-3/20"
                : step < filled
                  ? look.bar
                  : "bg-selection-strong",
            )}
          />
        ))}
      </span>
      <span
        className={cn(
          "hidden truncate font-mono text-[11.5px] sm:block",
          area.state === "running" ? "text-foreground" : "text-ink-3",
        )}
        title={area.now}
      >
        {area.now || (area.state === "kept" ? "same files as last learn" : "")}
      </span>
      <span className="text-end font-mono text-[11.5px] text-ink-2">{tokens(area.tokens)}</span>
      <span className="hidden text-end font-mono text-[11.5px] text-ink-2 sm:block">
        {seconds != null ? `${Math.round(seconds)}s` : "—"}
      </span>
      <span
        className={cn(
          "justify-self-end rounded-full px-2 py-0.5 text-[11.5px] font-medium",
          look.pill,
        )}
      >
        {look.label}
      </span>
    </li>
  );
}
