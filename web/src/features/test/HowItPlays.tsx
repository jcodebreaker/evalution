import { useEffect, useState } from "react";
import type { Scope } from "../../lib/api";
import { api, errorText } from "../../lib/api";
import type { CaseFileView, Keep, Proof, TestProof } from "../../lib/schema";
import { TextLink } from "../shared/group";
import { ProofControl } from "../shared/ProofControl";

/** How a test plays, in a sentence. */
export function playsWords(plays: CaseFileView["plays"]): string {
  switch (plays) {
    case "page":
      return "Played through the page: types each message, checks it shows.";
    case "page_only":
      return "Played through the page alone: types each message, checks it shows. A pass is reply only.";
    default:
      return "Played through the API.";
  }
}

/**
 * Under "What it checks": how the test plays and, through the page, what each try keeps. Each
 * part follows the app's setting until the test says otherwise; through the API there is nothing
 * to capture, so no setting shows.
 */
export function HowItPlays({
  scope,
  id,
  plays,
  proof,
}: {
  scope: Scope;
  id: string;
  plays: CaseFileView["plays"];
  proof: TestProof | null;
}) {
  const [shown, setShown] = useState<TestProof | null>(proof);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setShown(proof), [proof]);

  const save = async (part: keyof Proof, keep: Keep | null) => {
    setSaving(true);
    setError(null);
    try {
      setShown((await api.setTestProof(scope, id, { [part]: keep })).proof);
    } catch (failure) {
      setError(errorText(failure, "Couldn't change what this test keeps."));
    }
    setSaving(false);
  };

  return (
    <section className="flex flex-col gap-2.5 text-[14px]" data-testid="how-it-plays">
      <p className="m-0 text-ink-2">{playsWords(plays)}</p>
      {plays !== "api" && shown ? (
        <ProofControl
          proof={shown}
          disabled={saving}
          onChange={(part, keep) => void save(part, keep)}
          aside={(part) =>
            shown.own[part] ? (
              <>
                This test's own ·{" "}
                <TextLink
                  className="text-[12.5px]"
                  disabled={saving}
                  onClick={() => void save(part, null)}
                >
                  Use the app's
                </TextLink>
              </>
            ) : (
              "As the app keeps it"
            )
          }
        />
      ) : null}
      {error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null}
    </section>
  );
}
