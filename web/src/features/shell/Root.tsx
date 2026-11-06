import { useEffect, useState } from "react";
import { api, onSignedOut } from "../../lib/api";
import { currentWorkspace } from "../../lib/router";
import { ready, useLoad } from "../../lib/use-load";
import type { Account } from "../account/account";
import { AccountContext } from "../account/account";
import { Invite, inviteCode } from "../account/Invite";
import { SignIn } from "../account/SignIn";
import { App } from "./App";

/**
 * Who is asking, before the page. Locally (or a server that doesn't say) the page as it always was;
 * hosted, sign-in when no one is signed in, and the account for the shell and Settings.
 */
export function Root() {
  const [load, reload] = useLoad("me", api.me);
  const [signedOut, setSignedOut] = useState(false);
  useEffect(() => {
    onSignedOut(() => setSignedOut(true));
    return () => onSignedOut(null);
  }, []);

  const me = ready(load);
  const workspace = me?.hosted ? (me.workspace?.id ?? null) : null;
  // Hosted, the URL names the workspace from the start, so every request carries it.
  useEffect(() => {
    if (!workspace || currentWorkspace()) return;
    const params = new URLSearchParams(window.location.search);
    params.set("workspace", workspace);
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }, [workspace]);
  // A workspace the person isn't in (any more): their own instead.
  useEffect(() => {
    if (load.status === "missing" && currentWorkspace()) window.location.replace("/");
  }, [load.status]);

  const code = inviteCode();
  if (code !== null) {
    return <Invite code={code} me={load.status === "loading" ? undefined : me} />;
  }
  if (signedOut || (me?.hosted && !me.user)) return <SignIn />;
  const account: Account | null =
    me?.hosted && me.user && me.workspace
      ? { me: { ...me, user: me.user, workspace: me.workspace }, reload }
      : null;
  return (
    <AccountContext.Provider value={account}>
      <App />
    </AccountContext.Provider>
  );
}
