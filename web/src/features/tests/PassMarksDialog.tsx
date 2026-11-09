import { useState } from "react";
import type { Scope } from "../../lib/api";
import { api, errorText } from "../../lib/api";
import { score } from "../../lib/format";
import type { PassMarks } from "../../lib/schema";
import { Button } from "../../ui/button";
import { ConfirmDialog } from "../../ui/dialog";
import { Group } from "../shared/group";

/** The scores every try must reach to pass, for the whole set. */
export function PassMarksDialog({
  scope,
  marks,
  onClose,
  onSaved,
}: {
  scope: Scope;
  marks: PassMarks;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [scores, setScores] = useState(marks);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.passMarks(scope, scores);
      await onSaved();
    } catch (failure) {
      setError(errorText(failure, "Couldn't save the pass marks."));
      setBusy(false);
    }
  };
  return (
    <ConfirmDialog
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Pass marks"
      footer={
        <>
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={busy} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <Group>
        <ScoreSlider
          label="Steps score"
          value={scores.tools}
          onChange={(tools) => setScores((now) => ({ ...now, tools }))}
        />
        <ScoreSlider
          label="Reply score"
          value={scores.reply}
          onChange={(reply) => setScores((now) => ({ ...now, reply }))}
        />
      </Group>
      {error ? <p className="m-0 text-[13px] text-fail">{error}</p> : null}
    </ConfirmDialog>
  );
}

function ScoreSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="grid grid-cols-[minmax(0,1fr)_120px_34px] items-center gap-2.5 border-separator px-3.5 py-2.5 text-[14px] not-first:border-t">
      <span>{label}</span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        aria-label={`${label} pass mark`}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-tint"
      />
      <span className="text-end font-mono text-[12.5px] tabular-nums">{score(value)}</span>
    </label>
  );
}
