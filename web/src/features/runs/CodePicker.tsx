import { type FormEvent, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { RemoteBranches, Version, Versions } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Input, Select } from "../../ui/field";
import { useAccount } from "../account/account";
import { TextLink } from "../shared/group";
import { CodeLines } from "./Version";

/** What's on disk now: the default, and what a run played before versions could be picked. */
export const DISK = "disk";
const COMMIT = "commit";

/** The picker's value for *ref*: which option it is among those listed. */
function chosen(
  ref: string,
  refs: Versions | null,
  remote: RemoteBranches | null,
  typing: boolean,
): string {
  if (typing) return COMMIT;
  if (ref === DISK) return DISK;
  if (remote?.branches.some((b) => b.ref === ref)) return `remote:${ref}`;
  const found = refs?.refs.find((r) => r.name === ref);
  if (found?.kind === "local") return `local:${ref}`;
  if (found?.kind === "tag") return `tag:${ref}`;
  if (found) return `ref:${ref}`;
  return COMMIT;
}

/**
 * The Run dialog's Code line (ADR 0011): what the run plays and, for a version other than the
 * disk, what checking it out and building it costs. "Change" opens the picker: what's on disk now,
 * the repository's branches, the remote's branches as they are now (listed with `git ls-remote`
 * when it opens, fetched into evalution's own refs when one is picked), tags, or a commit. Local
 * only: hosted evalution has no copies to play a version on.
 */
export function CodePicker({
  app,
  picked,
  version,
  onRef,
}: {
  app: string;
  /** The version picked: `disk`, or a branch, `origin/branch`, tag or commit. */
  picked: string;
  version: Version | null | undefined;
  onRef: (ref: string) => void;
}) {
  const hosted = useAccount() !== null;
  const [open, setOpen] = useState(false);
  const [refs, setRefs] = useState<Versions | null>(null);
  const [remote, setRemote] = useState<RemoteBranches | null>(null);
  const [listing, setListing] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState<string | null>(null);
  /** The commit being typed, once "A commit…" is picked; `null` otherwise. */
  const [commit, setCommit] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setListing(true);
    api.versions(app).then(
      (found) => {
        if (!cancelled) setRefs(found);
      },
      (failure: unknown) => {
        if (!cancelled) setError(errorText(failure, "Couldn't read the app's branches."));
      },
    );
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
  }, [open, app]);

  const pick = async (value: string) => {
    setError(null);
    if (value === COMMIT) {
      setCommit("");
      return;
    }
    setCommit(null);
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
    onRef(value === DISK ? DISK : value.slice(value.indexOf(":") + 1));
  };

  const submitCommit = (event: FormEvent) => {
    event.preventDefault();
    const typed = commit?.trim();
    if (typed) onRef(typed);
  };

  const locals = refs?.refs.filter((r) => r.kind === "local") ?? [];
  const tags = refs?.refs.filter((r) => r.kind === "tag") ?? [];
  // When the remote can't be listed, its branches as last fetched here.
  const tracked = remote ? [] : (refs?.refs.filter((r) => r.kind === "remote") ?? []);
  const here = refs?.checked_out?.branch;

  return (
    <div className="flex flex-col items-center gap-1.5 text-center" data-testid="code-picker">
      <CodeLines version={version} className="items-center text-center" />
      {hosted ? null : open ? (
        <div className="flex w-full max-w-[360px] flex-col gap-1.5 text-[13px]">
          <Select
            aria-label="Code to run"
            value={chosen(picked, refs, remote, commit !== null)}
            disabled={fetching !== null}
            onChange={(event) => void pick(event.target.value)}
          >
            <option value={DISK}>What's on disk now</option>
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
            <option value={COMMIT}>A commit…</option>
          </Select>
          {commit !== null ? (
            <form className="flex gap-1.5" onSubmit={submitCommit}>
              <Input
                aria-label="Commit"
                placeholder="a1b2c3d"
                autoFocus
                value={commit}
                onChange={(event) => setCommit(event.target.value)}
              />
              <Button size="sm" type="submit" disabled={!commit.trim()}>
                Use
              </Button>
            </form>
          ) : null}
          {listing ? <p className="m-0 text-ink-3">Listing the remote's branches…</p> : null}
          {remoteError ? <p className="m-0 text-ink-3">{remoteError}</p> : null}
          {fetching ? <p className="m-0 text-ink-3">{`Fetching ${fetching}…`}</p> : null}
          {error ? <p className="m-0 text-destructive">{error}</p> : null}
        </div>
      ) : (
        <TextLink className="text-[13px]" onClick={() => setOpen(true)}>
          Run another version
        </TextLink>
      )}
    </div>
  );
}
