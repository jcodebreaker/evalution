import { Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { ago } from "../../lib/format";
import type { Member } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { CopyField } from "../../ui/copy-field";
import { ConfirmDialog } from "../../ui/dialog";
import { Input } from "../../ui/field";
import { Group, GroupHeading } from "../shared/group";
import { Row } from "../shared/Row";
import type { Account } from "./account";

const LINE =
  "flex min-w-0 items-center gap-3 border-separator px-3.5 py-2 text-[13px] not-first:border-t";
const TOKEN_GRID =
  "grid grid-cols-[minmax(0,1fr)_96px_96px_96px_28px] items-center gap-3 max-sm:grid-cols-[minmax(0,1fr)_96px_28px]";

/** Settings, hosted: the workspace's name, its members and invite link, and its API tokens. */
export function WorkspaceSettings({ account }: { account: Account }) {
  const { workspace } = account.me;
  const [error, setError] = useState<string | null>(null);
  /** Runs `work`, showing why when it fails. */
  const act = async (work: () => Promise<unknown>, fallback: string) => {
    setError(null);
    try {
      await work();
    } catch (failure) {
      setError(errorText(failure, fallback));
    }
  };
  return (
    <div className="flex flex-col gap-4">
      <h2 className="m-0 px-1 text-[13px] font-semibold">Workspace</h2>
      <Group>
        <Row label="Name">
          <Input
            key={workspace.name}
            defaultValue={workspace.name}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            onBlur={(event) => {
              const name = event.target.value.trim();
              if (name && name !== workspace.name)
                void act(async () => {
                  await api.workspaces.rename(workspace.id, name);
                  await account.reload();
                }, "Couldn't rename it.");
            }}
          />
        </Row>
      </Group>
      <Members account={account} act={act} />
      <Tokens act={act} />
      {error ? <p className="m-0 px-1 text-[12.5px] text-destructive">{error}</p> : null}
    </div>
  );
}

type Act = (work: () => Promise<unknown>, fallback: string) => Promise<void>;

function Members({ account, act }: { account: Account; act: Act }) {
  const { workspace, user } = account.me;
  const [load, reload] = useLoad(`members:${workspace.id}`, () =>
    api.workspaces.members(workspace.id),
  );
  const [link, setLink] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const members = load.status === "ready" ? load.data : [];
  const leaving = removing?.id === user.id;
  return (
    <div className="flex flex-col">
      <GroupHeading
        title="Members"
        aside={
          <Button
            variant="secondary"
            size="xs"
            onClick={() =>
              void act(
                async () => setLink((await api.workspaces.invite(workspace.id)).url),
                "Couldn't make an invite.",
              )
            }
          >
            Invite
          </Button>
        }
      />
      <Group>
        {link ? (
          <div className={LINE}>
            <CopyField value={link} label="Invite link" />
          </div>
        ) : null}
        {members.map((member) => (
          <div key={member.id} className={LINE}>
            <span className="min-w-0 truncate">{member.name || member.email}</span>
            {member.name ? (
              <span className="min-w-0 truncate text-ink-3">{member.email}</span>
            ) : null}
            {member.id === user.id && workspace.personal ? null : (
              <Button
                variant="ghost"
                size="xs"
                className="ms-auto text-ink-2"
                onClick={() => setRemoving(member)}
              >
                {member.id === user.id ? "Leave" : "Remove"}
              </Button>
            )}
          </div>
        ))}
      </Group>
      {removing ? (
        <ConfirmDialog
          onClose={() => setRemoving(null)}
          title={
            leaving ? `Leave ${workspace.name}?` : `Remove ${removing.name || removing.email}?`
          }
          footer={
            <>
              <Button variant="secondary" onClick={() => setRemoving(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  const member = removing;
                  setRemoving(null);
                  void act(async () => {
                    await api.workspaces.remove(workspace.id, member.id);
                    if (leaving) window.location.assign("/");
                    else await reload();
                  }, "Couldn't remove them.");
                }}
              >
                {leaving ? "Leave" : "Remove"}
              </Button>
            </>
          }
        />
      ) : null}
    </div>
  );
}

function Tokens({ act }: { act: Act }) {
  const [load, reload] = useLoad("tokens", api.tokens.list);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  /** The token just made: its value is sent once, and kept only here. */
  const [created, setCreated] = useState<string | null>(null);
  const tokens = load.status === "ready" ? load.data : [];
  const create = () =>
    act(async () => {
      const made = await api.tokens.create(name.trim());
      setCreated(made.token);
      setNaming(false);
      setName("");
      await reload();
    }, "Couldn't make the token.");
  return (
    <div className="flex flex-col">
      <GroupHeading
        title="API tokens"
        aside={
          <Button
            variant="secondary"
            size="xs"
            onClick={() => {
              setNaming(true);
              setCreated(null);
            }}
          >
            New token
          </Button>
        }
      />
      {naming || created || tokens.length ? (
        <Group>
          {naming ? (
            <form
              className={LINE}
              onSubmit={(event) => {
                event.preventDefault();
                if (name.trim()) void create();
              }}
            >
              <Input
                aria-label="Token name"
                placeholder="Name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoFocus
              />
              <Button type="submit" size="sm" disabled={!name.trim()}>
                Create
              </Button>
            </form>
          ) : null}
          {created ? (
            <div className={LINE}>
              <CopyField value={created} label="New token" />
            </div>
          ) : null}
          {tokens.length ? (
            <div
              className={`${TOKEN_GRID} border-separator px-3.5 pt-2 pb-1 text-[10.5px] font-medium tracking-[0.06em] text-ink-3 uppercase not-first:border-t`}
            >
              <span>Name</span>
              <span>Token</span>
              <span className="max-sm:hidden">Created</span>
              <span className="max-sm:hidden">Last used</span>
              <span />
            </div>
          ) : null}
          {tokens.map((token) => (
            <div key={token.id} className={`${TOKEN_GRID} px-3.5 py-1.5 text-[13px] tabular-nums`}>
              <span className="truncate">{token.name}</span>
              <span className="truncate font-mono text-[12px] text-ink-2">{token.prefix}…</span>
              <span className="truncate text-ink-2 max-sm:hidden">{ago(token.created_at)}</span>
              <span className="truncate text-ink-2 max-sm:hidden">
                {token.last_used_at ? ago(token.last_used_at) : "Never"}
              </span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Delete ${token.name}`}
                className="text-ink-2"
                onClick={() =>
                  void act(async () => {
                    await api.tokens.remove(token.id);
                    await reload();
                  }, "Couldn't delete it.")
                }
              >
                <Trash2 />
              </Button>
            </div>
          ))}
        </Group>
      ) : null}
    </div>
  );
}
