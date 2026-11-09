import { Check, Download } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { InstallJob, MissingPiece, UiTesting } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { CopyField } from "../../ui/copy-field";
import { Spinner } from "../../ui/loading";
import { useAccount } from "../account/account";
import { Checklist, Mark } from "../environment/Checklist";

/** How often the page reads the install again while it goes. */
export const INSTALL_POLL_MS = 1_000;
/** Lines of what the install printed that the page shows, the newest last. */
const LINES_SHOWN = 4;

const PART: Record<MissingPiece["part"], string> = {
  copy: "a copy of the app",
  playwright: "Playwright",
  browser: "a browser",
  ffmpeg: "ffmpeg",
};

/** Said under a command to copy: where it runs, and what then. */
export const RUN_IT = "Run it where evalution is installed, then try again.";

/**
 * What UI testing has on the server's computer, read again every second while an install goes,
 * and `install`, which starts one there. `null` while it's read, or hosted, where there's no
 * computer of one's own to install on. `onReady`: called once an install leaves nothing missing.
 */
export function useUiTesting(onReady?: () => void) {
  const hosted = useAccount() !== null;
  const [data, setData] = useState<UiTesting | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const ready = useRef(onReady);
  ready.current = onReady;
  const was = useRef<string | null>(null);
  const read = useCallback(async () => {
    try {
      setData(await api.uiTesting());
      setFailed(false);
    } catch {
      setData(null);
      setFailed(true);
    }
  }, []);
  useEffect(() => {
    if (!hosted) void read();
  }, [hosted, read]);
  const state = data?.job?.state ?? null;
  useEffect(() => {
    // An install that ended with everything there: whoever asked reads what changed.
    if (was.current === "running" && state === "done" && data?.ready) ready.current?.();
    was.current = state;
  }, [state, data?.ready]);
  useVisiblePoll(read, INSTALL_POLL_MS, state === "running");
  const install = async () => {
    setStarting(true);
    setError(null);
    try {
      const started = await api.installUiTesting();
      was.current = started.job?.state ?? null;
      setData(started);
      if (started.ready) ready.current?.();
    } catch (failure) {
      setError(errorText(failure, "Couldn't start the install."));
    }
    setStarting(false);
  };
  return { data: hosted ? null : data, failed, error, starting, install };
}

/** How far the running download is, from the last line it printed ("… 62% of 124.3 MiB"). */
export function percentOf(job: InstallJob | null): number | null {
  if (job?.state !== "running") return null;
  const found = /(\d{1,3})%/.exec(job.lines[job.lines.length - 1] ?? "");
  const percent = found ? Number(found[1]) : null;
  return percent !== null && percent <= 100 ? percent : null;
}

/** An install as it goes: a step per piece (unless `steps` is false, where the pieces are listed
 * with their state already), and the last lines it printed. */
export function InstallProgress({ job, steps = true }: { job: InstallJob; steps?: boolean }) {
  const lines = job.lines.slice(-LINES_SHOWN);
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="install-progress">
      {steps ? <Checklist steps={job.steps} /> : null}
      {job.state === "running" && lines.length ? (
        <pre
          className="m-0 overflow-hidden rounded-md bg-bubble px-2.5 py-1.5 font-mono text-[11.5px] leading-snug text-ink-2"
          role="log"
          aria-label="What the install says"
        >
          {lines.join("\n")}
        </pre>
      ) : null}
      {job.state === "failed" && job.error ? <p className="m-0 text-warning">{job.error}</p> : null}
      {job.state === "failed" && lines.length ? (
        <pre className="m-0 overflow-hidden rounded-md bg-bubble px-2.5 py-1.5 font-mono text-[11.5px] leading-snug text-ink-2">
          {lines.join("\n")}
        </pre>
      ) : null}
    </div>
  );
}

/** The command that installs *piece*, to copy and run by hand. */
function ByHand({ piece }: { piece: MissingPiece }) {
  if (piece.command === null) return null;
  return (
    <>
      <CopyField value={piece.command} label={`The command that installs ${PART[piece.part]}`} />
      <p className="m-0 text-ink-3">{RUN_IT}</p>
    </>
  );
}

/**
 * The Install button for what UI testing lacks (`pieces`), with the install's progress, where the
 * server can install it on its own computer; the exact command to copy stays below as the way by
 * hand. Where it can't, the command alone, with why. `onInstalled`: called once nothing is
 * missing.
 */
export function InstallPieces({
  pieces,
  onInstalled,
  className,
}: {
  pieces: readonly MissingPiece[];
  onInstalled?: () => void;
  className?: string;
}) {
  const ui = useUiTesting(onInstalled);
  return <InstallWith ui={ui} pieces={pieces} className={className} />;
}

/** {@link InstallPieces} on a reading already in hand. `steps`: show the install's steps. */
function InstallWith({
  ui,
  pieces,
  steps = true,
  className,
}: {
  ui: ReturnType<typeof useUiTesting>;
  pieces: readonly MissingPiece[];
  steps?: boolean;
  className?: string;
}) {
  const { data, error, starting, install } = ui;
  const job = data?.job ?? null;
  const running = job?.state === "running";
  const installed = job?.state === "done" && data?.ready;
  const byHand = pieces.filter((piece) => piece.command !== null);
  if (installed) {
    return (
      <div className={cn("flex min-w-0 flex-col gap-2 text-[13px]", className)}>
        <InstallProgress job={job} steps={steps} />
        <p className="m-0 flex items-center gap-1.5 text-success" data-testid="ui-installed">
          <Check className="size-3.5" aria-hidden />
          Installed. UI testing is ready.
        </p>
      </div>
    );
  }
  if (!data?.can_install && !running) {
    return (
      <div className={cn("flex min-w-0 flex-col gap-2 text-[13px]", className)}>
        {data?.why_not_install ? <p className="m-0 text-ink-2">{data.why_not_install}</p> : null}
        {byHand.map((piece) => (
          <ByHand key={piece.part} piece={piece} />
        ))}
      </div>
    );
  }
  return (
    <div className={cn("flex min-w-0 flex-col gap-2 text-[13px]", className)}>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          disabled={running || starting}
          onClick={() => void install()}
          data-testid="ui-install"
        >
          {running || starting ? <Spinner /> : <Download />}
          {running ? "Installing…" : job?.state === "failed" ? "Try again" : "Install"}
        </Button>
        <span className="text-ink-3">
          On this computer, as ./install.sh does. A browser is about 150 MB, once.
        </span>
      </div>
      {job ? <InstallProgress job={job} steps={steps} /> : null}
      {error ? <p className="m-0 text-destructive">{error}</p> : null}
      {byHand.length ? (
        <details className="text-ink-2">
          <summary className="cursor-pointer select-none">Or run it yourself</summary>
          <div className="mt-2 flex flex-col gap-2">
            {byHand.map((piece) => (
              <ByHand key={piece.part} piece={piece} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

type Part = keyof NonNullable<UiTesting["parts"]>;

/** Each piece UI testing needs: its name and what it's for. */
const PARTS: { part: Part; name: string; said: string }[] = [
  { part: "playwright", name: "Playwright", said: "Drives the browser." },
  {
    part: "browser",
    name: "A browser",
    said: "Chromium or Chrome, which plays tests on the page.",
  },
  {
    part: "ffmpeg",
    name: "ffmpeg",
    said: "For video of a try. Without it, tests still play, with screenshots only.",
  },
];

const STATE_WORDS = {
  done: "Installed.",
  running: "Installing…",
  waiting: "Next.",
  failed: "Didn't install.",
  missing: "Not installed.",
} as const;

/** Every piece UI testing needs, each there, missing, or as the install has it now, with how
 * far the running download is. */
function Parts({ data }: { data: UiTesting }) {
  const parts = data.parts;
  if (!parts) return null;
  const job = data.job;
  const percent = percentOf(job);
  return (
    <ul
      className="m-0 flex list-none flex-col gap-2.5 p-0"
      aria-label="What UI testing needs"
      data-testid="ui-parts"
    >
      {PARTS.map(({ part, name, said }) => {
        const step = job?.steps.find((each) => each.key === part);
        const state =
          step && (job?.state === "running" || step.state === "failed")
            ? step.state
            : parts[part]
              ? "done"
              : "missing";
        return (
          <li key={part} className="flex items-start gap-2.5" data-part={part} data-state={state}>
            <span className="pt-px">
              <Mark state={state === "missing" ? "waiting" : state} />
            </span>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="font-medium">{name}</span>
              <span className="text-ink-3">
                {said} {STATE_WORDS[state]}
              </span>
              {state === "running" && percent !== null ? (
                <div
                  role="progressbar"
                  aria-label={`Installing ${name}`}
                  aria-valuenow={percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  className="mt-1.5 h-1.5 max-w-[360px] overflow-hidden rounded-full bg-bubble"
                >
                  <div className="h-full rounded-full bg-tint" style={{ width: `${percent}%` }} />
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Settings → UI testing: each piece it needs on this computer, there or not, and one Install for
 * what's missing, with its progress and the last lines it printed. */
export function UiTestingStatus() {
  const ui = useUiTesting();
  const shown = ui.data;
  if (!shown) {
    return (
      <span className="pt-1.5 text-[14px] text-ink-3">
        {ui.failed ? "Couldn't read what UI testing has here." : "…"}
      </span>
    );
  }
  const running = shown.job?.state === "running";
  return (
    <div className="flex min-w-0 flex-col gap-2.5 pt-1.5 text-[13px]">
      <p className="m-0 max-w-[68ch] text-ink-2">
        What playing tests through an app's page needs on this computer.
      </p>
      {shown.parts ? (
        <Parts data={shown} />
      ) : (
        shown.missing.map((piece) => (
          <p key={piece.part} className="m-0 max-w-[68ch]">
            {piece.said}
          </p>
        ))
      )}
      {shown.ready && !running ? (
        <span className="flex items-center gap-1.5" data-testid="ui-testing-ready">
          <Check className="size-3.5 text-success" aria-hidden />
          Ready: tests can play through a page here.
        </span>
      ) : (
        <InstallWith ui={ui} pieces={shown.missing} steps={!shown.parts} />
      )}
    </div>
  );
}
