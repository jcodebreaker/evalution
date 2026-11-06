import type { ReactNode } from "react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import type { TargetBody, TargetDetail } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { CopyField } from "../../ui/copy-field";
import { Input, Select, Textarea } from "../../ui/field";
import { Spinner } from "../../ui/loading";
import { Group, GroupHeading } from "../shared/group";
import { KEY_LEFT_BEHIND } from "../shared/hosts";
import { Row } from "../shared/Row";
import { CheckResult } from "./CheckResult";
import type { Form, TextKey } from "./target-form";
import { BLANK, bodyOf, formOf, HTTP, labelOf, secretsLeftBehind } from "./target-form";
import { useCheck } from "./use-check";

/**
 * The target form: the app, where its traces go and, optionally, its code. The generic HTTP adapter
 * has its own fields; any other draws what it declares. Check tries the form as it stands; the main
 * action saves it.
 */
export function TargetFields({
  saved,
  fresh = false,
  action,
  onSave,
  extra,
}: {
  saved?: TargetDetail;
  /** `saved` is a proposal not saved yet: its name may still change. */
  fresh?: boolean;
  action: string;
  onSave: (body: TargetBody) => Promise<void>;
  extra?: ReactNode;
}) {
  const [form, setForm] = useState<Form>(saved ? formOf(saved) : BLANK);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checker = useCheck();
  const [traces] = useLoad("traces", api.traces);
  const [adapterList] = useLoad("adapters", api.adapters);
  const adapters = adapterList.status === "ready" ? adapterList.data : [];
  const info = adapters.find((entry) => entry.name === form.adapter);
  // Hosted, apps send traces here with a token: say where.
  const endpoint =
    traces.status === "ready" && traces.data.token ? `${traces.data.url}/v1/traces` : null;
  const own = form.ownPhoenix;
  const set = (patch: Partial<Form>) => setForm((now) => ({ ...now, ...patch }));
  const field = (key: TextKey) => ({
    value: form[key],
    onChange: (event: { target: { value: string } }) => set({ [key]: event.target.value }),
  });
  const has = (key: string) => saved?.secrets.includes(key) ?? false;
  const kept = fresh ? "From the project" : "Saved";
  // Options the form doesn't show (set by hand) are sent back as they were.
  const keep = saved?.adapter === form.adapter ? saved.options : {};
  const http = form.adapter === HTTP;
  const secretKeys = http ? ["authorization"] : (info?.secrets ?? []);
  const changing = !!saved && form.adapter !== saved.adapter;
  const asItStands = saved && !fresh ? bodyOf(form, keep) : null;
  const leavesKey =
    saved && asItStands && typeof asItStands !== "string"
      ? secretsLeftBehind(saved, asItStands).length > 0
      : false;

  const body = () => {
    const made = bodyOf(form, keep);
    if (typeof made === "string") setError(made);
    return typeof made === "string" ? null : made;
  };
  const check = () => {
    setError(null);
    const made = body();
    if (made) void checker.run(made.name || "new", made);
  };
  const save = async () => {
    setError(null);
    const made = body();
    if (!made) return;
    setBusy(true);
    try {
      await onSave(made);
    } catch (failure) {
      setError(errorText(failure, "Couldn't save."));
    }
    setBusy(false);
  };

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <Group>
        <Row label="Name">
          <Input
            {...field("name")}
            required
            disabled={!!saved && !fresh}
            placeholder="my-app"
            autoComplete="off"
          />
        </Row>
        <Row
          label="Type"
          hint={
            changing
              ? "This changes what is tested. Earlier results won't be comparable, and the test message has to pass again."
              : undefined
          }
        >
          <Select
            value={form.adapter}
            onChange={(event) => set({ adapter: event.target.value, options: {}, secrets: {} })}
          >
            {adapters.length ? null : <option value={form.adapter}>{form.adapter}</option>}
            {adapters.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {entry.label}
              </option>
            ))}
          </Select>
        </Row>
        {http ? (
          <>
            <Row label="URL">
              <div className="flex gap-2">
                <Select {...field("method")} className="w-24 shrink-0" aria-label="Method">
                  <option>POST</option>
                  <option>PUT</option>
                  <option>GET</option>
                </Select>
                <Input
                  {...field("url")}
                  required
                  type="url"
                  placeholder="http://localhost:3000/chat"
                />
              </div>
            </Row>
            <Row label="Body" hint="{message} and {session} are filled in">
              <Textarea {...field("body")} spellCheck={false} className="font-mono text-[12px]" />
            </Row>
            <Row label="Reply" hint="Where the answer is, like $.reply">
              <Input {...field("reply")} className="font-mono text-[12px]" />
            </Row>
            <Row label="Done">
              <Select
                value={form.poll || form.until ? "poll" : "response"}
                onChange={(event) =>
                  set(
                    event.target.value === "poll"
                      ? { until: "$.status == 'idle'" }
                      : { poll: "", until: "" },
                  )
                }
              >
                <option value="response">When it responds</option>
                <option value="poll">When a status URL says so</option>
              </Select>
            </Row>
            {form.poll || form.until ? (
              <>
                <Row label="Status URL">
                  <Input {...field("poll")} type="url" required />
                </Row>
                <Row label="Until">
                  <Input {...field("until")} required className="font-mono text-[12px]" />
                </Row>
              </>
            ) : null}
          </>
        ) : (
          (info?.options ?? []).map((key) => {
            const required = info?.required.includes(key) ?? false;
            return (
              <Row key={key} label={labelOf(key)}>
                <Input
                  value={form.options[key] ?? ""}
                  onChange={(event) =>
                    set({ options: { ...form.options, [key]: event.target.value } })
                  }
                  required={required}
                  autoComplete="off"
                  placeholder={required ? "" : "Optional"}
                />
              </Row>
            );
          })
        )}
        {secretKeys.map((key) => (
          <Row key={key} label={labelOf(key)}>
            <Input
              value={form.secrets[key] ?? ""}
              onChange={(event) => set({ secrets: { ...form.secrets, [key]: event.target.value } })}
              type="password"
              autoComplete="off"
              placeholder={has(key) ? kept : "Optional"}
            />
          </Row>
        ))}
      </Group>

      <section>
        <GroupHeading title="Traces" />
        <Group>
          {endpoint ? (
            <Row label="Send to" hint="With a traces token from Settings">
              <CopyField value={endpoint} label="Traces endpoint" />
            </Row>
          ) : null}
          <label className="flex items-center gap-2.5 px-3.5 py-2.5 text-[13px]">
            <input
              type="checkbox"
              checked={form.ownPhoenix}
              onChange={(event) => set({ ownPhoenix: event.target.checked })}
            />
            Use my existing Phoenix
          </label>
          {own ? (
            <>
              <Row label="URL">
                <Input {...field("phoenixUrl")} required type="url" />
              </Row>
              <Row label="Project">
                <Input {...field("project")} required />
              </Row>
              <Row label="API key">
                <Input
                  {...field("phoenixKey")}
                  type="password"
                  autoComplete="off"
                  placeholder={has("phoenix_api_key") ? kept : "Optional"}
                />
              </Row>
            </>
          ) : null}
        </Group>
      </section>

      <section>
        <GroupHeading title="Code" />
        <Group>
          <Row label="Folder">
            <Input {...field("codePath")} placeholder="Optional" />
          </Row>
          {form.codePath ? (
            <Row label="Ignore">
              <Input {...field("ignore")} className="font-mono text-[12px]" />
            </Row>
          ) : null}
        </Group>
      </section>

      {leavesKey ? <p className="m-0 text-[12.5px] text-ink-2">{KEY_LEFT_BEHIND}</p> : null}
      {checker.check ? <CheckResult check={checker.check} /> : null}
      {checker.error || error ? (
        <p className="m-0 text-[12.5px] text-destructive">{checker.error ?? error}</p>
      ) : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {extra}
        <Button variant="secondary" disabled={checker.checking} onClick={check}>
          {checker.checking ? <Spinner /> : null}
          Check connection
        </Button>
        <Button type="submit" disabled={busy}>
          {action}
        </Button>
      </div>
    </form>
  );
}
