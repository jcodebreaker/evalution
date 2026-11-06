import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { ModelBody, ModelSettings, ModelTest, Provider } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
import { Spinner } from "../../ui/loading";
import { useAccount } from "../account/account";
import { Group } from "../shared/group";
import { hostOf, KEY_LEFT_BEHIND } from "../shared/hosts";
import { Row } from "../shared/Row";
import { ModelPicker } from "./ModelPicker";
import { TestResult } from "./TestResult";

const PROVIDERS: { value: Provider; label: string }[] = [
  { value: "anthropic", label: "Claude" },
  { value: "openai", label: "OpenAI" },
  { value: "compatible", label: "Other OpenAI-compatible" },
];

export function ModelForm({
  saved,
  onSaved,
}: {
  saved: ModelSettings;
  /** After a save, and whether a stored key stayed behind. */
  onSaved: (keyDropped: boolean) => Promise<unknown>;
}) {
  const fixed = saved.source === "config";
  // Hosted, the key is the signed-in person's own, kept apart from the workspace's model choice.
  const account = useAccount();
  const ownKey = account?.me.model_key ?? null;
  const [provider, setProvider] = useState(saved.provider);
  const [baseUrl, setBaseUrl] = useState(saved.provider === "compatible" ? saved.base_url : "");
  const [key, setKey] = useState("");
  /** The key the model list was asked with: set when the field is left. */
  const [listKey, setListKey] = useState("");
  const [strong, setStrong] = useState(saved.strong);
  const [light, setLight] = useState(saved.light);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<ModelTest | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** What the last save did. */
  const [outcome, setOutcome] = useState<string | null>(null);

  /** The address the model list was asked at: set when the field is left, as typing goes on. */
  const [listUrl, setListUrl] = useState(baseUrl);
  const [listError, setListError] = useState<string | null>(null);
  /** Only when the address that answered isn't the one typed. */
  const [note, setNote] = useState<string | null>(null);

  const url = provider === "compatible" ? baseUrl.trim() : "";
  const asked = provider === "compatible" ? listUrl.trim() : "";
  const canList = provider !== "compatible" || /^https?:\/\/./.test(asked);
  const [models] = useLoad(canList ? `models:${provider}:${asked}:${listKey}` : null, async () => {
    try {
      const found = await api.model.list(provider, asked, listKey);
      setListError(null);
      if (found.note) setNote(found.note);
      if (asked && found.base_url && found.base_url !== asked) {
        setBaseUrl(found.base_url);
        setListUrl(found.base_url);
      }
      return found.data;
    } catch (failure) {
      setListError(errorText(failure, "Couldn't list the models."));
      throw failure;
    }
  });
  const options = models.status === "ready" ? models.data : null;

  const form = (withKey = true): ModelBody => ({
    provider,
    ...(url ? { base_url: url } : {}),
    strong,
    light,
    ...(key && withKey ? { key } : {}),
  });
  const runTest = async () => {
    setTesting(true);
    setError(null);
    try {
      setTest(await api.model.test(fixed ? undefined : form()));
    } catch (failure) {
      setTest(null);
      setError(errorText(failure, "Couldn't test the model."));
    }
    setTesting(false);
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      if (account && key) {
        await api.modelKey.set(key);
        await account.reload();
      }
      const done = await api.model.save(form(!account));
      setKey("");
      setOutcome(done.key_dropped ? `Saved. ${KEY_LEFT_BEHIND}` : "Saved.");
      await onSaved(done.key_dropped);
    } catch (failure) {
      setError(errorText(failure, "Couldn't save."));
    }
    setBusy(false);
  };

  // As the server decides it: a key stored by a form stays with its host. One in the environment
  // is the owner's own, and goes wherever they point it.
  const host = hostOf(url || (saved.addresses[provider] ?? ""));
  const moved = !!saved.strong && !!host && host !== hostOf(saved.base_url);
  const stored = saved.source === "page" && saved.key_set;
  const keyHint = ownKey
    ? ownKey.set
      ? `•••• ${ownKey.last4 ?? ""}`
      : ""
    : saved.source === "env"
      ? "From EVALUTION_MODEL_KEY"
      : stored && !moved
        ? "Saved"
        : "";
  const deleteKey = async () => {
    if (!account) return;
    setError(null);
    try {
      await api.modelKey.remove();
      await account.reload();
    } catch (failure) {
      setError(errorText(failure, "Couldn't delete the key."));
    }
  };

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      {fixed ? <p className="m-0 text-[13px] text-ink-2">Set in evalution.yaml.</p> : null}
      <Group>
        <Row label="Provider" group>
          <span className="flex flex-wrap gap-0.5 self-start rounded-lg bg-selection p-0.5">
            {PROVIDERS.map((entry) => (
              <button
                key={entry.value}
                type="button"
                disabled={fixed}
                aria-pressed={provider === entry.value}
                onClick={() => {
                  setProvider(entry.value);
                  setTest(null);
                }}
                className={cn(
                  "h-7 rounded-md px-2.5 text-[12.5px] focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50",
                  provider === entry.value
                    ? "bg-background font-medium shadow-sm"
                    : "text-ink-2 hover:text-foreground",
                )}
              >
                {entry.label}
              </button>
            ))}
          </span>
        </Row>
        {provider === "compatible" ? (
          <Row label="Base URL">
            <Input
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value);
                setNote(null);
              }}
              onBlur={() => setListUrl(baseUrl.trim())}
              disabled={fixed}
              required
              type="url"
              placeholder="http://localhost:11434/v1"
            />
          </Row>
        ) : null}
        {fixed ? null : (
          <Row label="API key">
            <span className="flex min-w-0 items-center gap-2">
              <Input
                value={key}
                onChange={(event) => setKey(event.target.value)}
                onBlur={() => setListKey(key.trim())}
                disabled={!account && saved.source === "env"}
                type="password"
                autoComplete="off"
                placeholder={keyHint}
              />
              {ownKey?.set ? (
                <Button variant="ghost" size="sm" onClick={() => void deleteKey()}>
                  Delete
                </Button>
              ) : null}
            </span>
          </Row>
        )}
        <Row label="For thinking" hint="Writing tests, reviewing runs, grading">
          <ModelPicker
            value={strong}
            options={options}
            disabled={fixed}
            label="Model for thinking"
            onChange={setStrong}
          />
        </Row>
        <Row label="For filler" hint="Filler messages in long tests: a cheap one is fine">
          <ModelPicker
            value={light}
            options={options}
            disabled={fixed}
            label="Model for filler"
            onChange={setLight}
          />
        </Row>
      </Group>
      {models.status === "error" && canList && !fixed ? (
        <p className="m-0 text-[12.5px] text-ink-2">{listError} Type their names, or fix it.</p>
      ) : null}
      {note ? <p className="m-0 text-[12.5px] text-ink-2">{note}</p> : null}
      {!account && stored && moved && !key.trim() ? (
        <p className="m-0 text-[12.5px] text-ink-2">{KEY_LEFT_BEHIND}</p>
      ) : outcome ? (
        <p className="m-0 text-[12.5px] text-ink-2">{outcome}</p>
      ) : null}

      {test ? <TestResult test={test} /> : null}
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button
          variant="secondary"
          disabled={testing || !strong || !light}
          onClick={() => void runTest()}
        >
          {testing ? <Spinner /> : null}
          Test model
        </Button>
        {fixed ? null : (
          <Button type="submit" disabled={busy || !strong || !light}>
            Save
          </Button>
        )}
      </div>
    </form>
  );
}
