import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import { openWorkspace } from "../../lib/router";
import type { Me } from "../../lib/schema";
import { buttonClass } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { SignIn } from "./SignIn";

/** `/invite/<code>`, the code, else `null`. */
export function inviteCode(): string | null {
  const found = /^\/invite\/([^/]+)\/?$/.exec(window.location.pathname);
  return found?.[1] ? decodeURIComponent(found[1]) : null;
}

/**
 * An invite link: signed in, joins and opens that workspace; signed out, signs in first and comes
 * back here. `me` is `undefined` while it is read, `null` when there is no account to join with.
 */
export function Invite({ code, me }: { code: string; me: Me | null | undefined }) {
  const [error, setError] = useState<string | null>(null);
  const asked = useRef(false);
  const signedIn = !!me?.hosted && !!me.user;

  useEffect(() => {
    if (me === undefined) return;
    if (!me?.hosted) {
      window.location.replace("/");
      return;
    }
    if (!signedIn || asked.current) return;
    asked.current = true;
    api.workspaces.accept(code).then(
      ({ workspace }) => openWorkspace(workspace.id),
      (failure) => setError(errorText(failure, "This invite didn't work.")),
    );
  }, [code, me, signedIn]);

  if (me?.hosted && !me.user) return <SignIn />;
  return (
    <div className="canvas grid h-full place-items-center text-[13.5px] text-foreground">
      {error ? (
        <div className="flex flex-col items-center gap-4">
          <span className="text-ink-2">{error}</span>
          <a href="/" className={buttonClass("secondary")}>
            Home
          </a>
        </div>
      ) : (
        <Spinner />
      )}
    </div>
  );
}
