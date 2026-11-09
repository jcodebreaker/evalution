import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { Build, BuiltVersion } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { Checklist } from "./Checklist";

/** How often the card reads the job again while it proves the recipe. */
export const BUILD_POLL_MS = 1_000;
/** The build's last lines shown while it goes. */
const LINES_SHOWN = 8;

/** Bytes in words: "19.5 GB". */
export const sizeWords = (bytes: number) => `${(bytes / 1000 ** 3).toFixed(1)} GB`;

/** Seconds in words: "3 min 53 s", or "45 s". */
export function buildTime(seconds: number) {
  const whole = Math.max(1, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  return minutes ? `${minutes} min ${whole % 60} s` : `${whole} s`;
}

/** What a built version took, in a sentence part: "in 3 min 53 s, taking 19.5 GB". */
function took(built: BuiltVersion) {
  const parts = [];
  if (built.seconds !== null) parts.push(`in ${buildTime(built.seconds)}`);
  const size = built.disk_bytes || built.image_bytes;
  if (size) parts.push(`taking ${sizeWords(size)}`);
  return parts.join(", ");
}

/** Where the recipe stands, in a sentence. */
function standing(build: Build): { text: string; tone: string } | null {
  switch (build.status) {
    case "proposed":
      return {
        text: "Proposed, not proven yet: proving builds what's on disk once and starts a copy from it.",
        tone: "text-ink-2",
      };
    case "proven":
      return {
        text: "Proven: what's on disk was built this way and a copy started from it.",
        tone: "text-ink-2",
      };
    case "stale":
      return {
        text: `Out of date: ${build.stale_files.join(", ") || "a file it rests on"} changed since it was proven. Prove it again before a version is built with it.`,
        tone: "text-warning",
      };
    case "failed":
      return { text: "The last proof didn't work: see why below.", tone: "text-destructive" };
    default:
      return null;
  }
}

function Badge({ status }: { status: Build["status"] }) {
  const words =
    status === "proven"
      ? { text: "Proven", tone: "bg-up text-success" }
      : status === "stale"
        ? { text: "Out of date", tone: "bg-warning/10 text-warning" }
        : status === "failed"
          ? { text: "Not proven", tone: "bg-destructive/10 text-destructive" }
          : status === "proposed"
            ? { text: "Proposed", tone: "bg-bubble text-ink-2" }
            : null;
  if (!words) return null;
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", words.tone)}>
      {words.text}
    </span>
  );
}

/**
 * The step "How a version is built": the recipe that builds any version of the app's code into
 * evalution's own names (never the person's images) in plain words, what it rests on, the last
 * build's time and size, and Propose, Prove (its cost said first) and Undo. Proving is a job the
 * card follows: its steps and the build's last lines.
 */
export function BuildRecipeCard({ target }: { target: string }) {
  const [build, setBuild] = useState<Build | null>(null);
  const [failed, setFailed] = useState(false);
  const [working, setWorking] = useState<"propose" | "prove" | "undo" | "remove" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const read = useCallback(async () => {
    try {
      setBuild(await api.build(target));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [target]);
  useEffect(() => {
    setBuild(null);
    void read();
  }, [read]);
  const running = build?.job?.state === "running";
  useVisiblePoll(read, BUILD_POLL_MS, running);

  if (!build) {
    return failed ? (
      <p className="m-0 text-[14px] text-ink-2">
        Couldn't load how a version of {target} is built.
      </p>
    ) : null;
  }

  const act = async (
    kind: "propose" | "prove" | "undo" | "remove",
    work: () => Promise<Build>,
    fallback: string,
  ) => {
    setWorking(kind);
    setError(null);
    try {
      setBuild(await work());
    } catch (failure) {
      setError(errorText(failure, fallback));
    }
    setWorking(null);
  };
  const { recipe, job } = build;
  const said = standing(build);
  const low = build.free_bytes < build.min_free_bytes;
  const lines = job?.lines.slice(-LINES_SHOWN) ?? [];
  const busy = working !== null || running;

  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-[14px] border border-border bg-group p-5 text-[14px] max-md:p-4"
      aria-label="How a version is built"
      data-testid="build-recipe"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="m-0 text-[15px] font-semibold">How a version is built</h3>
        <Badge status={build.status} />
      </div>

      {recipe ? (
        <>
          <p className="m-0 max-w-[68ch]" data-testid="build-words">
            {recipe.words}
          </p>
          {said ? <p className={cn("m-0 max-w-[68ch]", said.tone)}>{said.text}</p> : null}
          {recipe.by === "agent" && recipe.why ? (
            <p className="m-0 text-[13px] text-ink-2">Adjusted by the test writer: {recipe.why}</p>
          ) : null}
          {recipe.evidence.length ? (
            <div className="flex flex-col gap-1">
              <p className="m-0 font-medium">What it rests on</p>
              <ul className="m-0 flex list-none flex-col gap-1 p-0" aria-label="What it rests on">
                {recipe.evidence.map((claim) => (
                  <li key={`${claim.at} ${claim.says}`} className="flex min-w-0 flex-wrap gap-x-2">
                    <span className="font-mono text-[12px] text-ink-3">{claim.at}</span>
                    <span className="min-w-0 text-[13px] text-ink-2">{claim.says}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : (
        <p className="m-0 max-w-[68ch] text-ink-2">
          Evalution reads {target}'s files and proposes how to build any version of its code into
          its own names, so your images are never touched. Proposing builds nothing.
        </p>
      )}

      {build.last_build && !running ? (
        <p className="m-0 text-[13px] text-ink-2" data-testid="last-build">
          Last built {took(build.last_build)} of this computer's disk.
        </p>
      ) : null}

      {build.built.length ? (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Built versions">
          {build.built.map((built) => (
            <li key={built.tag} className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="min-w-0 truncate font-mono text-[12px]">{built.tag}</span>
              <span className="text-[12.5px] text-ink-3">
                {sizeWords(built.disk_bytes || built.image_bytes || 0)}
              </span>
              <Button
                variant="ghost"
                size="xs"
                disabled={busy}
                onClick={() =>
                  void act(
                    "remove",
                    () => api.removeBuild(target, built.tag),
                    "Couldn't remove it.",
                  )
                }
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {build.removed ? (
        <p className="m-0 text-[13px] text-ink-2">
          Removed {build.removed.images.join(", ") || "nothing"}. {build.removed.note}
        </p>
      ) : null}

      {job && job.state !== "done" ? (
        <div className="flex min-w-0 flex-col gap-2">
          <Checklist steps={job.steps} />
          {lines.length && job.state === "running" ? (
            <pre
              className="m-0 overflow-hidden rounded-md bg-bubble px-2.5 py-1.5 font-mono text-[11.5px] leading-snug text-ink-2"
              role="log"
              aria-label="What the build says"
            >
              {lines.join("\n")}
            </pre>
          ) : null}
          {job.state === "failed" && job.error ? (
            <p className="m-0 text-[13px] whitespace-pre-wrap text-warning">{job.error}</p>
          ) : null}
        </div>
      ) : null}
      {build.status === "failed" && job?.error && job.state === "done" ? (
        <p className="m-0 text-[13px] whitespace-pre-wrap text-destructive">{job.error}</p>
      ) : null}

      {low ? (
        <p className="m-0 text-[13px] text-warning" data-testid="low-disk">
          Only {sizeWords(build.free_bytes)} is free on this computer's disk, and a build needs{" "}
          {sizeWords(build.min_free_bytes)}: remove built versions or free space elsewhere first.
        </p>
      ) : null}
      {build.problem ? <p className="m-0 text-destructive">{build.problem}</p> : null}

      {running ? null : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-3">
            {recipe ? (
              <Button
                variant={build.status === "proven" ? "secondary" : "default"}
                disabled={!build.can_prove || busy}
                onClick={() =>
                  void act(
                    "prove",
                    () => api.proveBuild(target),
                    "Couldn't start. Nothing was built.",
                  )
                }
              >
                {working === "prove" ? <Spinner /> : null}
                {build.status === "proven" || build.status === "stale"
                  ? "Prove it again"
                  : "Prove it"}
              </Button>
            ) : null}
            <Button
              variant={recipe ? "ghost" : "default"}
              disabled={!build.can_propose || busy}
              onClick={() =>
                void act("propose", () => api.proposeBuild(target), "Couldn't propose one.")
              }
            >
              {working === "propose" ? <Spinner /> : null}
              {recipe ? "Propose again" : "Propose"}
            </Button>
            {build.can_undo ? (
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => void act("undo", () => api.undoBuild(target), "Couldn't undo it.")}
              >
                Undo the last recipe
              </Button>
            ) : null}
          </div>
          {recipe && build.cost ? (
            <p className="m-0 max-w-[68ch] text-[12.5px] text-ink-3" data-testid="build-cost">
              Proving {build.cost}.
            </p>
          ) : null}
          {!build.can_propose ? (
            <p className="m-0 text-[12.5px] text-ink-3">
              Say where the app's code is first: the recipe is found from it.
            </p>
          ) : null}
        </div>
      )}
      {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
    </section>
  );
}
