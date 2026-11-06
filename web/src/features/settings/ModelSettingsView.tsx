import type { ReactNode } from "react";
import { api } from "../../lib/api";
import { usd } from "../../lib/format";
import { useLoad } from "../../lib/use-load";
import { Skeleton } from "../../ui/field";
import { useAccount } from "../account/account";
import { WorkspaceSettings } from "../account/WorkspaceSettings";
import { Centered, Page, Toolbar, ToolbarTitle } from "../shared/layout";
import { AppSettings } from "./AppSettings";
import { ModelForm } from "./ModelForm";

/** Settings, Model: the model evalution uses to write tests, review runs, grade and fill. */
export function ModelSettingsView({ target }: { target: string | null }) {
  const [load, reload] = useLoad("settings:model", api.model.get);
  const saved = load.status === "ready" ? load.data : null;
  const account = useAccount();

  let body: ReactNode;
  if (load.status === "loading") body = <Skeleton className="h-80 rounded-xl" />;
  else if (!saved) body = <Centered>Couldn't load the model.</Centered>;
  else body = <ModelForm key={saved.source} saved={saved} onSaved={reload} />;

  return (
    <>
      <Toolbar>
        <ToolbarTitle title="Settings" />
      </Toolbar>
      <Page narrow>
        {body}
        {saved ? (
          <p className="m-0 px-1 text-[12.5px] text-ink-3">Spent so far: {usd(saved.spent_usd)}</p>
        ) : null}
        {target ? <AppSettings key={target} target={target} /> : null}
        {account ? <WorkspaceSettings account={account} /> : null}
      </Page>
    </>
  );
}
