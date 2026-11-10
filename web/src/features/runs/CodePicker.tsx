import { Check } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { FetchedPull, RemoteBranches, Version, VersionsNext } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Input, Select } from "../../ui/field";
import { useAccount } from "../account/account";
import { Chip, Seg } from "../shared/kit";
import { CodeLines } from "./Version";

/** What's on disk now: the default, and what a run played before versions could be picked. */
export const DISK = "disk";

/** How the code to play is picked: what's on disk, a branch (or tag), a pull request, a commit. */
export type CodeWay = "disk" | "branch" | "pull" | "commit";

/** A pull request's number in a ref a run plays it by: `pull/412`, `#412`; else `null`. */
export function pullOf(ref: string): number | null {
  const found = /^(?:pull\/|#)(\d+)$/.exec(ref.trim());
  return found ? Number(found[1]) : null;
}

/** The way a picked ref was picked, as far as the ref alone tells. */
export function wayOf(ref: string): CodeWay {
  if (ref === DISK) return "disk";
  if (pullOf(ref) !== null) return "pull";
  if (/^[0-9a-f]{7,40}$/.test(ref)) return "commit";
  return "branch";
}

/** The picker's value for a branch-like *ref*: which option it is among those listed. */
function chosen(ref: string, refs: VersionsNext | null, remote: RemoteBranches | null): string {
  if (remote?.branches.some((b) => b.ref === ref)) return `remote:${ref}`;
  const found = refs?.refs.find((r) => r.name === ref);
  if (found?.kind === "local") return `local:${ref}`;
  if (found?.kind === "tag") return `tag:${ref}`;
  if (found) return `ref:${ref}`;
  return "";
}

/** The repository's refs, read once a way that lists them is picked. */
function useRefs(app: string, on: boolean) {
  const [refs, setRefs] = useState<VersionsNext | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let cancelled = false;
    api.versionsNext(app).then(
      (found) => {
        if (!cancelled) setRefs(found);
      },
      (failure: unknown) => {
        if (!cancelled) setError(errorText(failure, "Couldn't read the app's branches."));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [app, on]);
  return { refs, error, setRefs };
}

/**
 * The Run dialog's Code row (ADR 0011, docs/versions.md): what the run plays, picked as what's
 * on disk now, a branch (the repository's own, the remote's as they are now, fetched into
 * evalution's own refs when picked, or a tag), a pull request by its number (fetched into
 * evalution's refs, then played as `pull/<n>`), or a commit; and, for a version other than the
 * disk, what checking it out and building it costs. Local only: hosted evalution has no copies to
 * play a version on.
 */
export function CodePicker({
  app,
  picked,
  version,
  onRef,
}: {
  app: string;
  /** The version picked: `disk`, or a branch, `origin/branch`, tag, `pull/<n>` or commit. */
  picked: string;
  version: Version | null | undefined;
  onRef: (ref: string) => void;
}) {
  const hosted = useAccount() !== null;
  const [way, setWay] = useState<CodeWay>(() => wayOf(picked));
  const { refs, error: refsError, setRefs } = useRefs(app, way === "branch" || way === "pull");
  const [remote, setRemote] = useState<RemoteBranches | null>(null);
  const [listing, setListing] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState<string | null>(null);
  const [commit, setCommit] = useState(way === "commit" ? picked : "");
  const [pullText, setPullText] = useState(() => String(pullOf(picked) ?? ""));
  const [pulled, setPulled] = useState<FetchedPull | null>(null);

  // The remote's branches are listed only once Branch is picked: `ls-remote` is a network call.
  const branching = way === "branch";
  useEffect(() => {
    if (!branching) return;
    let cancelled = false;
    setListing(true);
    api.remoteBranches(app).then(
      (found) => {
        if (cancelled) return;
        setRemote(found);
        setListing(false);
      },
      (failure: unknown) => {
        if (cancelled) return;
        setRemoteError(errorText(failure, "Couldn't list the remote's branches."));
        setListing(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [branching, app]);

  const changeWay = (next: CodeWay) => {
    setWay(next);
    setError(null);
    if (next === "disk") onRef(DISK);
  };

  const pick = async (value: string) => {
    setError(null);
    if (!value) return;
    if (value.startsWith("remote:")) {
      const branch = remote?.branches.find((b) => `remote:${b.ref}` === value);
      if (!branch) return;
      setFetching(branch.ref);
      try {
        await api.fetchBranch(app, branch.remote, branch.name);
        onRef(branch.ref);
      } catch (failure) {
        setError(errorText(failure, `Couldn't fetch ${branch.ref}.`));
      }
      setFetching(null);
      return;
    }
    onRef(value.slice(value.indexOf(":") + 1));
  };

  const fetchPull = async (event: FormEvent) => {
    event.preventDefault();
    const n = Number(pullText.replace(/^#/, "").trim());
    if (!Number.isInteger(n) || n < 1) {
      setError("A pull request's number, such as 412.");
      return;
    }
    setError(null);
    setFetching(`#${n}`);
    try {
      const found = await api.fetchPull(app, n);
      setPulled(found);
      onRef(found.ref);
      // Read the refs again: the pull request is listed among them now, with its subject.
      api.versionsNext(app).then(setRefs, () => undefined);
    } catch (failure) {
      setError(errorText(failure, `Couldn't fetch pull request #${n}.`));
    }
    setFetching(null);
  };

  const submitCommit = (event: FormEvent) => {
    event.preventDefault();
    const typed = commit.trim();
    if (typed) onRef(typed);
  };

  const locals = refs?.refs.filter((r) => r.kind === "local") ?? [];
  const tags = refs?.refs.filter((r) => r.kind === "tag") ?? [];
  const pulls = refs?.refs.filter((r) => r.kind === "pull") ?? [];
  // When the remote can't be listed, its branches as last fetched here.
  const tracked = remote ? [] : (refs?.refs.filter((r) => r.kind === "remote") ?? []);
  const here = refs?.checked_out?.branch;
  const pullN = pullOf(picked);
  const pull =
    pullN === null
      ? null
      : (pulls.find((r) => pullOf(r.name) === pullN || r.ref.endsWith(`/pull/${pullN}`)) ?? null);
  const pullCommit =
    (pulled && pullOf(pulled.ref) === pullN ? pulled.commit : pull?.commit) ?? null;

  return (
    <div className="flex flex-col items-start gap-2.5" data-testid="code-picker">
      {hosted ? null : (
        <Seg
          label="Code"
          value={way}
          onChange={changeWay}
          options={[
            { value: "disk", label: "On disk now" },
            { value: "branch", label: "Branch" },
            { value: "pull", label: "Pull request" },
            { value: "commit", label: "Commit" },
          ]}
        />
      )}
      {way === "branch" && !hosted ? (
        <div className="flex w-full max-w-[420px] flex-col gap-1.5 text-sm">
          <Select
            aria-label="Code to run"
            className="h-10 rounded-[10px]"
            value={chosen(picked, refs, remote)}
            disabled={fetching !== null}
            onChange={(event) => void pick(event.target.value)}
          >
            <option value="" disabled>
              Pick a branch
            </option>
            {locals.length ? (
              <optgroup label="Branches">
                {locals.map((r) => (
                  <option key={r.ref} value={`local:${r.name}`}>
                    {r.name === here ? `${r.name} (checked out)` : r.name}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {remote?.branches.length ? (
              <optgroup label={`On ${remote.remote}`}>
                {remote.branches.map((b) => (
                  <option key={b.ref} value={`remote:${b.ref}`}>
                    {b.ref}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {tracked.length ? (
              <optgroup label="Remote branches, as last fetched">
                {tracked.map((r) => (
                  <option key={r.ref} value={`ref:${r.name}`}>
                    {r.name}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {tags.length ? (
              <optgroup label="Tags">
                {tags.map((r) => (
                  <option key={r.ref} value={`tag:${r.name}`}>
                    {r.name}
                  </option>
                ))}
              </optgroup>
            ) : null}
          </Select>
          {listing ? <p className="m-0 text-ink-3">Listing the remote's branches…</p> : null}
          {remoteError ? <p className="m-0 text-ink-3">{remoteError}</p> : null}
        </div>
      ) : null}
      {way === "pull" && !hosted ? (
        <div className="flex w-full flex-col gap-2 text-sm">
          <form
            className="flex h-10 w-full max-w-[420px] items-center gap-2 rounded-[10px] border border-rule-strong bg-group ps-3 pe-1 focus-within:border-tint"
            onSubmit={(event) => void fetchPull(event)}
          >
            <span className="font-mono text-ink-3">#</span>
            <input
              aria-label="Pull request number"
              inputMode="numeric"
              placeholder="412"
              value={pullText}
              onChange={(event) => setPullText(event.target.value)}
              className="min-w-0 flex-1 border-0 bg-transparent font-mono outline-0"
            />
            <Button size="sm" type="submit" disabled={!pullText.trim() || fetching !== null}>
              Fetch
            </Button>
          </form>
          {pullN !== null && (pull || pullCommit) ? (
            <div className="flex flex-wrap items-center gap-2" data-testid="pull-picked">
              <Check aria-hidden className="size-[15px] text-pass" />
              <span className="font-mono">{`#${pullN}`}</span>
              {pull?.subject ? <span className="min-w-0 truncate">{pull.subject}</span> : null}
              {pullCommit ? <Chip mono>{pullCommit.slice(0, 7)}</Chip> : null}
            </div>
          ) : null}
          {pulls.length ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-ink-3">Fetched before</span>
              {pulls.map((r) => (
                <button
                  key={r.ref}
                  type="button"
                  onClick={() => {
                    const n = pullOf(r.name) ?? Number(/(\d+)$/.exec(r.ref)?.[1]);
                    if (!n) return;
                    setPullText(String(n));
                    onRef(`pull/${n}`);
                  }}
                  title={r.subject}
                  className="inline-flex h-7 items-center rounded-full border border-rule bg-group px-2.5 font-mono text-xs text-ink-2 hover:border-tint hover:text-tint-ink focus-visible:outline-2 focus-visible:outline-tint"
                >
                  {r.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {way === "commit" && !hosted ? (
        <form className="flex w-full max-w-[420px] gap-1.5" onSubmit={submitCommit}>
          <Input
            aria-label="Commit"
            placeholder="a1b2c3d"
            className="h-10 rounded-[10px] font-mono"
            value={commit}
            onChange={(event) => setCommit(event.target.value)}
          />
          <Button type="submit" className="h-10" disabled={!commit.trim()}>
            Use
          </Button>
        </form>
      ) : null}
      {fetching ? <p className="m-0 text-sm text-ink-3">{`Fetching ${fetching}…`}</p> : null}
      {error || refsError ? <p className="m-0 text-sm text-fail">{error ?? refsError}</p> : null}
      <CodeLines version={version} className="w-full" />
    </div>
  );
}
