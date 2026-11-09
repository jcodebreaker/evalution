import { cn } from "../../lib/cn";
import type { Version } from "../../lib/schema";

/** How much of a commit is shown. */
const SHORT = 7;

/** A run's code in a few characters: "page-testing @ e535c4d", or the commit alone. */
export function versionChip(version: Version | null | undefined): string | null {
  const commit = version?.commit?.slice(0, SHORT);
  if (!commit) return null;
  return version?.branch ? `${version.branch} @ ${commit}` : commit;
}

/** The code a run plays or played, in a line, and why evalution can't vouch for it when it can't:
 * a warning when what played may be another version, a quiet note when it can't tell. */
export function CodeLines({
  version,
  className,
}: {
  version: Version | null | undefined;
  className?: string;
}) {
  if (!version?.said) return null;
  const why = version.why_unverified;
  return (
    <div className={cn("flex flex-col gap-0.5 text-[13px]", className)} data-testid="code-lines">
      <p className="m-0 text-ink-2">{`Code: ${version.said}`}</p>
      {version.cost ? (
        <p className="m-0 text-ink-2" data-testid="version-cost">
          {version.cost}
        </p>
      ) : null}
      {why && version.verified === false ? (
        <p className="m-0 text-warning">{`Can't vouch for this: ${why}.`}</p>
      ) : why && version.verified === null && version.ref === "disk" ? (
        <p className="m-0 text-ink-3">{`${why[0]?.toUpperCase()}${why.slice(1)}.`}</p>
      ) : null}
      {version.notes.map((note) => (
        <p key={note} className="m-0 text-warning">
          {note}
        </p>
      ))}
    </div>
  );
}

/** The run's code as a small chip in a list of runs. */
export function VersionChip({ version }: { version: Version | null | undefined }) {
  const said = versionChip(version);
  if (!said) return null;
  return (
    <span
      title={version?.said ? `Code: ${version.said}` : undefined}
      data-testid="version-chip"
      className={cn(
        "shrink-0 rounded-[6px] bg-bubble px-1.5 py-px font-mono text-[11.5px] text-ink-2",
        version?.verified === false && "text-warning",
      )}
    >
      {said}
    </span>
  );
}

/** Why a run of a version ended before any try: it couldn't be checked out or built. Nothing
 * played, so nothing is judged; the reason, then the build's last lines as it printed them. */
export function NotBuilt({ reason }: { reason: string }) {
  const [head = "", ...lines] = reason.split("\n");
  return (
    <div className="flex flex-col gap-1.5 text-[13px]" data-testid="version-not-built">
      <p className="m-0 text-warning">{`Nothing played, so nothing was judged: ${head}`}</p>
      {lines.length ? (
        <pre className="m-0 overflow-x-auto rounded-lg bg-bubble px-3 py-2 font-mono text-[12px] leading-[1.45] whitespace-pre-wrap text-ink-2">
          {lines.join("\n")}
        </pre>
      ) : null}
    </div>
  );
}
