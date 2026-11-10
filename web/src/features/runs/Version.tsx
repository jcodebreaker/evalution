import { cn } from "../../lib/cn";
import type { Version } from "../../lib/schema";
import { Chip, Notice } from "./parts";

/** How much of a commit is shown. */
const SHORT = 7;

/** A run's code in a few characters: "page-testing @ e535c4d", or the commit alone. */
export function versionChip(version: Version | null | undefined): string | null {
  const commit = version?.commit?.slice(0, SHORT);
  if (!commit) return null;
  return version?.branch ? `${version.branch} @ ${commit}` : commit;
}

const capital = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

/** The code a run plays or played, as the server says it ("What was on disk: page-testing @
 * e535c4d, with uncommitted changes"), under a label that says it is the code; what checking it
 * out costs; and why evalution can't vouch for it when it can't: an amber notice when what played
 * may be another version, a quiet note when it can't tell. */
export function CodeLines({
  version,
  className,
}: {
  version: Version | null | undefined;
  className?: string;
}) {
  if (!version?.said) return null;
  const why = version.why_unverified;
  const warnings = [
    ...(why && version.verified === false ? [`Can't vouch for this: ${why}.`] : []),
    ...version.notes,
  ];
  return (
    <div className={cn("flex flex-col gap-1.5 text-[13px]", className)} data-testid="code-lines">
      <p className="m-0 text-foreground [overflow-wrap:anywhere]">{capital(version.said)}</p>
      {version.cost ? (
        <p className="m-0 text-ink-2" data-testid="version-cost">
          {version.cost}
        </p>
      ) : null}
      {why && version.verified === null && version.ref === "disk" ? (
        <p className="m-0 text-ink-3">{`${capital(why)}.`}</p>
      ) : null}
      {warnings.length ? (
        <Notice tone="amber" className="flex flex-col gap-1 px-3 py-2 text-start">
          {warnings.map((line) => (
            <p key={line} className="m-0 text-warning">
              {line}
            </p>
          ))}
        </Notice>
      ) : null}
    </div>
  );
}

/** What played, as the copy ran it: the images the app's own files build (or every image, when
 * none is the app's), or its working tree; `null` when evalution didn't start it. */
export function playedWords(version: Version | null | undefined): string[] | null {
  const played = version?.played;
  if (!played) return null;
  if (played.kind === "images") {
    const own = played.images.filter((image) => image.built);
    const names = (own.length ? own : played.images).map((image) => image.image);
    return names.length ? [...new Set(names)] : null;
  }
  if (played.kind === "tree") {
    return [
      version?.ref && version.ref !== "disk"
        ? "Its own checkout's files"
        : "The code folder's files",
    ];
  }
  return null;
}

/** The run's code as a small chip in a list of runs, or a run's header. */
export function VersionChip({ version }: { version: Version | null | undefined }) {
  const said = versionChip(version);
  if (!said) return null;
  return (
    <Chip
      mono
      title={version?.said ? `Code: ${version.said}` : undefined}
      data-testid="version-chip"
      className={cn("shrink-0", version?.verified === false && "text-warning")}
    >
      {said}
    </Chip>
  );
}

/** Why a run of a version ended before any try: it couldn't be checked out or built. Nothing
 * played, so nothing is judged; the reason, then the build's last lines as it printed them. */
export function NotBuilt({ reason }: { reason: string }) {
  const [head = "", ...lines] = reason.split("\n");
  return (
    <Notice tone="fail" className="flex flex-col gap-2" data-testid="version-not-built">
      <p className="m-0">
        <b className="font-semibold text-fail">Nothing played, so nothing was judged:</b> {head}
      </p>
      {lines.length ? (
        <pre className="m-0 overflow-x-auto rounded-lg bg-group/70 px-3 py-2 font-mono text-[13px] leading-[1.45] whitespace-pre-wrap text-ink-2">
          {lines.join("\n")}
        </pre>
      ) : null}
    </Notice>
  );
}
