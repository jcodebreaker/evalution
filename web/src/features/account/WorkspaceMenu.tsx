import { LogOut, Plus } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { openWorkspace } from "../../lib/router";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";
import { Input } from "../../ui/field";
import { Picker } from "../../ui/menu";
import type { Account } from "./account";

/** The shell's workspace switcher: who is signed in, their workspaces, a new one, sign out. */
export function WorkspaceMenu({ account }: { account: Account }) {
  const { me } = account;
  const [naming, setNaming] = useState(false);
  return (
    <>
      <Picker
        label="Workspace"
        value={me.workspace.name}
        header={me.user.email}
        options={me.workspaces.map((entry) => ({ value: entry.id, label: entry.name }))}
        onPick={(id) => {
          if (id !== me.workspace.id) openWorkspace(id);
        }}
        actions={[
          {
            label: (
              <>
                <Plus className="size-3.5" aria-hidden />
                New workspace
              </>
            ),
            onClick: () => setNaming(true),
          },
          {
            label: (
              <>
                <LogOut className="size-3.5" aria-hidden />
                Sign out
              </>
            ),
            onClick: () => void api.signOut().finally(() => window.location.assign("/")),
          },
        ]}
        className="-ms-1.5 text-[15px]"
      />
      {naming ? <NewWorkspace onClose={() => setNaming(false)} /> : null}
    </>
  );
}

function NewWorkspace({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      openWorkspace((await api.workspaces.create(name.trim())).id);
    } catch (failure) {
      setError(errorText(failure, "Couldn't create it."));
      setBusy(false);
    }
  };
  return (
    <ConfirmDialog
      onClose={onClose}
      title="New workspace"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="new-workspace" disabled={busy || !name.trim()}>
            Create
          </Button>
        </>
      }
    >
      <form
        id="new-workspace"
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <Input
          aria-label="Name"
          placeholder="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoFocus
        />
        {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
      </form>
    </ConfirmDialog>
  );
}
