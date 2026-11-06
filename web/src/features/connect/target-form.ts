import type { TargetBody, TargetDetail } from "../../lib/schema";
import { hostOf, hostsOf } from "../shared/hosts";

const DEFAULT_BODY = '{"message": "{message}", "session": "{session}"}';

/** The generic adapter, whose settings have their own fields. Any other draws what it declares. */
export const HTTP = "http";

export type Form = {
  name: string;
  adapter: string;
  url: string;
  method: string;
  body: string;
  reply: string;
  poll: string;
  until: string;
  /** Another adapter's options, by name. */
  options: Record<string, string>;
  /** Its secrets, by name, or the http adapter's `authorization`: empty keeps what is saved. */
  secrets: Record<string, string>;
  /** Read the app's own Phoenix too. */
  ownPhoenix: boolean;
  phoenixUrl: string;
  project: string;
  phoenixKey: string;
  codePath: string;
  ignore: string;
};

export type TextKey = { [K in keyof Form]: Form[K] extends string ? K : never }[keyof Form];

export const BLANK: Form = {
  name: "",
  adapter: HTTP,
  url: "",
  method: "POST",
  body: DEFAULT_BODY,
  reply: "$.reply",
  poll: "",
  until: "",
  options: {},
  secrets: {},
  ownPhoenix: false,
  phoenixUrl: "http://127.0.0.1:6006",
  project: "default",
  phoenixKey: "",
  codePath: "",
  ignore: "",
};

const str = (value: unknown) => (typeof value === "string" ? value : "");

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

/** A field's words from an option's or a secret's name: `assistant_id` is "Assistant id". */
export function labelOf(key: string): string {
  const words = key
    .replace(/_/g, " ")
    .replace(/\burl\b/, "URL")
    .replace(/\bapi\b/, "API");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The form of a saved target (its secrets stay empty: they are kept unless typed again). */
export function formOf(target: TargetDetail): Form {
  const { options } = target;
  const send = record(options.send);
  const poll = record(record(options.done).poll);
  const http = target.adapter === HTTP;
  return {
    ...BLANK,
    name: target.name,
    adapter: target.adapter,
    url: str(send.url),
    method: str(send.method) || "POST",
    body: send.body === undefined ? "" : JSON.stringify(send.body),
    reply: str(options.reply),
    poll: str(poll.url),
    until: str(poll.until),
    options: http
      ? {}
      : Object.fromEntries(
          Object.entries(options).filter((entry): entry is [string, string] => {
            return typeof entry[1] === "string";
          }),
        ),
    ownPhoenix: target.phoenix !== null,
    phoenixUrl: target.phoenix?.url ?? BLANK.phoenixUrl,
    project: target.phoenix?.project ?? BLANK.project,
    codePath: target.code?.path ?? "",
    ignore: target.code?.ignore.join(", ") ?? "",
  };
}

/** The body the server takes, or the reason the form can't be sent yet. */
export function bodyOf(form: Form, keep: Record<string, unknown>): TargetBody | string {
  let options: Record<string, unknown>;
  const secrets: Record<string, string> = {};
  for (const [key, value] of Object.entries(form.secrets)) {
    if (value) secrets[key] = value;
  }
  if (form.adapter === HTTP) {
    let body: unknown;
    if (form.body.trim()) {
      try {
        body = JSON.parse(form.body);
      } catch {
        return "The body isn't written correctly.";
      }
    }
    options = {
      ...keep,
      send: { ...record(keep.send), method: form.method, url: form.url.trim(), body },
      reply: form.reply.trim() || "$",
      done: form.poll.trim()
        ? { poll: { url: form.poll.trim(), until: form.until.trim() } }
        : "response",
    };
  } else {
    options = { ...keep };
    for (const [key, value] of Object.entries(form.options)) options[key] = value.trim();
  }
  const own = form.ownPhoenix;
  if (form.phoenixKey && own) secrets.phoenix_api_key = form.phoenixKey;
  const ignore = form.ignore
    .split(",")
    .map((glob) => glob.trim())
    .filter(Boolean);
  return {
    name: form.name.trim(),
    adapter: form.adapter,
    options,
    phoenix: own ? { url: form.phoenixUrl.trim(), project: form.project.trim() } : null,
    code: form.codePath.trim() ? { path: form.codePath.trim(), ignore } : null,
    secrets,
  };
}

/** The stored secrets a save of *body* leaves behind: a secret goes only to the hosts it was given
 * for, so a new host needs it typed again. */
export function secretsLeftBehind(saved: TargetDetail, body: TargetBody): string[] {
  const before = hostsOf(saved.options);
  const appMoved = [...hostsOf(body.options)].some((host) => !before.has(host));
  const traces = hostOf(body.phoenix?.url ?? "");
  const tracesMoved = !!traces && traces !== hostOf(saved.phoenix?.url ?? "");
  return saved.secrets.filter(
    (key) => !body.secrets[key] && (key === "phoenix_api_key" ? tracesMoved : appMoved),
  );
}
