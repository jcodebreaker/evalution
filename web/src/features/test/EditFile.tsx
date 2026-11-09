import { useEffect, useState } from "react";
import type { Scope } from "../../lib/api";
import { ApiError, api, errorText } from "../../lib/api";
import type { MeaningChange } from "../../lib/schema";
import { MeaningChangeSchema } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Sheet } from "../../ui/dialog";
import { Textarea } from "../../ui/field";
import { MeaningChanges } from "../shared/MeaningChanges";

/** The difference the server asks to be reviewed, with the token that saves that very text. */
function reviewOf(failure: unknown): { changes: MeaningChange[]; token: string } | null {
  if (!(failure instanceof ApiError) || failure.status !== 409) return null;
  const data = failure.data as { changes?: unknown; token?: unknown } | null;
  if (!Array.isArray(data?.changes) || typeof data?.token !== "string") return null;
  return {
    changes: data.changes.map((entry) => MeaningChangeSchema.parse(entry)),
    token: data.token,
  };
}

/** A test's file as written, edited by hand; saving says what is wrong when it isn't valid. */
export function EditFile({
  scope,
  id,
  onClose,
  onSaved,
}: {
  scope: Scope;
  id: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [review, setReview] = useState<ReturnType<typeof reviewOf>>(null);
  const { target, set } = scope;

  useEffect(() => {
    let cancelled = false;
    api.file({ target, set }, id).then(
      (found) => {
        if (!cancelled) setText(found);
      },
      (failure: unknown) => {
        if (!cancelled) setError(errorText(failure, "Couldn't open the file."));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [target, set, id]);

  const save = async () => {
    if (text === null) return;
    try {
      JSON.parse(text);
    } catch (failure) {
      setError(failure instanceof SyntaxError ? failure.message : "This isn't valid JSON.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.saveFile({ target, set }, id, text, review?.token);
      onSaved();
    } catch (failure) {
      const asked = reviewOf(failure);
      setReview(asked);
      if (!asked) setError(errorText(failure, "Couldn't save the file."));
      setBusy(false);
    }
  };

  return (
    <Sheet
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Edit file"
      footer={
        <>
          {error ? (
            <p className="m-0 me-auto min-w-0 self-center text-[13px] whitespace-pre-wrap text-destructive">
              {error}
            </p>
          ) : null}
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={busy || text === null} onClick={() => void save()}>
            {review ? "Save the change" : "Save"}
          </Button>
        </>
      }
    >
      {review ? <MeaningChanges changes={review.changes} /> : null}
      <Textarea
        value={text ?? ""}
        disabled={text === null}
        onChange={(event) => {
          setText(event.target.value);
          setReview(null);
        }}
        spellCheck={false}
        aria-label="Test file"
        className="min-h-0 flex-1 font-mono text-[12.5px] leading-[1.55]"
      />
    </Sheet>
  );
}
