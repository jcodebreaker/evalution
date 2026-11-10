import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { ModelForm } from "../settings/ModelForm";

/** Which model each part uses, or the form to choose them. */
export function ModelStep({ onChosen }: { onChosen: (chosen: boolean) => void }) {
  const [load, reload] = useLoad("settings:model", api.model.get);
  const [editing, setEditing] = useState(false);
  const saved = ready(load);
  const chosen = !!saved?.strong && !!saved.light;
  useEffect(() => onChosen(chosen), [chosen, onChosen]);

  if (load.status === "error" || load.status === "missing") {
    return <p className="m-0 text-sm text-ink-2">Couldn't load the model. Reload to retry.</p>;
  }
  if (!saved) return null;
  if (chosen && !editing) {
    return (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-2">
        <span className="min-w-0 break-words">
          Thinking: <span className="font-mono text-[13px] text-foreground">{saved.strong}</span>
        </span>
        <span className="min-w-0 break-words">
          Filler: <span className="font-mono text-[13px] text-foreground">{saved.light}</span>
        </span>
        <Button variant="ghost" size="xs" onClick={() => setEditing(true)}>
          Change
        </Button>
      </div>
    );
  }
  return (
    <ModelForm
      key={saved.source}
      saved={saved}
      onSaved={async (keyDropped) => {
        await reload();
        // A key left behind is said in the form, which stays open to type it.
        if (!keyDropped) setEditing(false);
      }}
    />
  );
}
