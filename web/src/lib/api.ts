import type * as z from "zod";
import { currentWorkspace } from "./router";
import type {
  AgentSession,
  Knowledge,
  ModelBody,
  PassMarks,
  Period,
  Steer,
  TargetBody,
  TokenScope,
  Understanding,
} from "./schema";
import {
  AcceptedSchema,
  AdapterSchema,
  AgentChatSchema,
  AgentSessionSchema,
  ApiTokenSchema,
  BenchmarksSchema,
  CaseFileViewSchema,
  CaseRowSchema,
  CompareSchema,
  ComparisonSchema,
  DiscoverySchema,
  EstimateSchema,
  HealthSchema,
  HooksSchema,
  InviteSchema,
  LibraryRowSchema,
  LineAgreementSchema,
  MemberSchema,
  MeSchema,
  ModelKeySchema,
  ModelListSchema,
  ModelSettingsSchema,
  ModelTestSchema,
  NewTokenSchema,
  ProbeSchema,
  RedraftSchema,
  RunCaseSchema,
  RunDetailSchema,
  RunSummarySchema,
  SetSummarySchema,
  SourceSchema,
  TargetCheckSchema,
  TargetDetailSchema,
  TargetSchema,
  TestPlanSchema,
  TracesSchema,
  UnderstandingSchema,
  UpdatesSchema,
  WorkspaceSchema,
} from "./schema";

// The server's routes (docs/architecture.md "API and page"). Locally the server answers only this
// computer, with no login. Hosted, each request names its workspace, and an answer that no one is
// signed in turns the page to the sign-in screen.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The body it came with, for an answer that says more than a sentence. */
    readonly data?: unknown,
  ) {
    super(message);
  }
}

/** A failure in words a person may read, else `fallback`. */
export function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.message && error.status < 500) return error.message;
  // fetch itself failed: the server is down or restarting, not a problem with the request.
  if (error instanceof TypeError)
    return "evalution isn't answering. It may be restarting; try again in a moment.";
  return fallback;
}

let signedOut: (() => void) | null = null;

/** `listener` hears a 401: hosted, no one is signed in (any more). */
export function onSignedOut(listener: (() => void) | null) {
  signedOut = listener;
}

/**
 * Every request: the workspace it is for, and the header that marks it as the page's own (the
 * server refuses a signed-in change without it, so another site can't make one).
 */
async function send(
  method: string,
  path: string,
  body: string | undefined,
  headers: Record<string, string>,
): Promise<Response> {
  const workspace = currentWorkspace();
  const response = await fetch(path, {
    method,
    headers: {
      "X-Requested-With": "evalution",
      ...(workspace ? { "X-Workspace": workspace } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    body,
  });
  if (response.status === 401) signedOut?.();
  return response;
}

async function call(
  method: string,
  path: string,
  body?: unknown,
  extra: Record<string, string> = {},
): Promise<unknown> {
  const response = await send(
    method,
    path,
    body === undefined ? undefined : JSON.stringify(body),
    extra,
  );
  const text = await response.text();
  const type = response.headers.get("content-type") ?? "";
  const data: unknown = type.includes("json") && text ? JSON.parse(text) : text;
  if (!response.ok) {
    throw new ApiError(response.status, detailText(data));
  }
  return data;
}

/** The server's `detail`: a sentence, or a list of validation errors, one per line. */
function detailText(data: unknown): string {
  const detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "";
  return detail
    .map((entry) => {
      if (typeof entry === "string") return entry;
      const { loc, msg } = (entry ?? {}) as { loc?: unknown; msg?: unknown };
      const where = Array.isArray(loc) ? loc.filter((part) => part !== "body").join(".") : "";
      return [where, typeof msg === "string" ? msg : ""].filter(Boolean).join(": ");
    })
    .filter(Boolean)
    .join("\n");
}

/** A call whose body, sent and answered, is the text itself. */
async function raw(
  method: string,
  path: string,
  text?: string,
  extra: Record<string, string> = {},
): Promise<string> {
  const response = await send(method, path, text, extra);
  const answer = await response.text();
  if (!response.ok) {
    let data: unknown = null;
    try {
      data = JSON.parse(answer);
    } catch {
      // Not JSON: no detail to show.
    }
    throw new ApiError(response.status, detailText(data), data);
  }
  return answer;
}

type Parse<T> = (data: unknown) => T;

const get = <T>(parse: Parse<T>, path: string) => call("GET", path).then(parse);
const post = <T>(parse: Parse<T>, path: string, body: unknown = {}) =>
  call("POST", path, body).then(parse);

const one =
  <T>(schema: z.ZodType<T>): Parse<T> =>
  (data) =>
    schema.parse(data);

/** `{data: T[]}` or a bare list. */
const many =
  <T>(schema: z.ZodType<T>): Parse<T[]> =>
  (data) => {
    const rows =
      data && typeof data === "object" && "data" in data ? (data as { data: unknown }).data : data;
    return Array.isArray(rows) ? rows.map((row) => schema.parse(row)) : [];
  };

/** `schema`'s value when the answer is one, else `null`: for answers the page can do without. */
const maybe =
  <T>(schema: z.ZodType<T>): Parse<T | null> =>
  (data) => {
    const parsed = schema.safeParse(data);
    return parsed.success ? parsed.data : null;
  };

const enc = encodeURIComponent;
const query = (params: Record<string, string | number | undefined | null>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
};

export type Scope = { target: string; set: string };

export const api = {
  /** Who is asking, in which workspace; locally, no one, in the one workspace. */
  me: () => get(one(MeSchema), "/v1/me"),
  signOut: () => call("POST", "/auth/logout"),
  workspaces: {
    create: (name: string) => post(one(WorkspaceSchema), "/v1/workspaces", { name }),
    rename: (id: string, name: string) =>
      call("PATCH", `/v1/workspaces/${enc(id)}`, { name }).then(one(WorkspaceSchema)),
    members: (id: string) => get(many(MemberSchema), `/v1/workspaces/${enc(id)}/members`),
    /** Anyone may remove anyone; removing yourself is leaving. */
    remove: (id: string, user: string) =>
      call("DELETE", `/v1/workspaces/${enc(id)}/members/${enc(user)}`),
    invite: (id: string) => post(one(InviteSchema), `/v1/workspaces/${enc(id)}/invites`),
    accept: (code: string) => post(one(AcceptedSchema), `/v1/invites/${enc(code)}/accept`),
  },
  /** The current workspace's tokens, for the CLI and CI. */
  tokens: {
    list: () => get(many(ApiTokenSchema), "/v1/tokens"),
    create: (name: string, scope: TokenScope) =>
      post(one(NewTokenSchema), "/v1/tokens", { name, scope }),
    remove: (id: string) => call("DELETE", `/v1/tokens/${enc(id)}`),
  },
  /** Hosted: the signed-in person's own model key. */
  modelKey: {
    set: (key: string) => call("PUT", "/v1/me/model-key", { key }).then(one(ModelKeySchema)),
    remove: () => call("DELETE", "/v1/me/model-key"),
  },
  targets: () => get(many(TargetSchema), "/v1/targets"),
  target: (name: string) => get(one(TargetDetailSchema), `/v1/targets/${enc(name)}`),
  connect: (body: TargetBody) => post(one(TargetDetailSchema), "/v1/targets", body),
  /** The kinds of app that can be connected, with what each takes. */
  adapters: () => get(many(AdapterSchema), "/v1/adapters"),
  /** Look at a project folder or an app's address; follow it with `discovery`. */
  discover: (path: string) => post(one(DiscoverySchema), "/v1/targets/discover", { path }),
  discovery: (id: string) => get(one(DiscoverySchema), `/v1/targets/discover/${enc(id)}`),
  /** Save what the folder showed, with answers to its questions, or the Details form. */
  /** Check again whether the app's traces reach evalution. */
  checkTraces: (id: string) =>
    post(one(DiscoverySchema), `/v1/targets/discover/${enc(id)}/traces`, {}),
  traces: () => get(one(TracesSchema), "/v1/traces"),
  accept: (id: string, choice: { answers?: Record<string, string>; target?: TargetBody }) =>
    post(one(TargetDetailSchema), `/v1/targets/discover/${enc(id)}/accept`, choice),
  updateTarget: (body: TargetBody) =>
    call("PUT", `/v1/targets/${enc(body.name)}`, body).then(one(TargetDetailSchema)),
  removeTarget: (name: string) => call("DELETE", `/v1/targets/${enc(name)}`),
  /** Each part of a target, tried with no message: the form as it stands, else the saved one. */
  reach: (name: string, body?: TargetBody) =>
    post(one(TargetCheckSchema), `/v1/targets/${enc(name)}/reach`, body),
  /** One real message through the saved target. */
  probe: (name: string) => post(one(ProbeSchema), `/v1/targets/${enc(name)}/check`),
  /** After a failed message, what the model tries instead, with up to two more; saves nothing. */
  redraft: (name: string) => post(one(RedraftSchema), `/v1/targets/${enc(name)}/check/redraft`),
  setIsolation: (name: string, required: string[]) =>
    call("PUT", `/v1/targets/${enc(name)}/isolation`, { required }),
  benchmarks: (target: string) => get(one(BenchmarksSchema), `/v1/benchmarks${query({ target })}`),
  /** Work out how the benchmark plays on this app, and try it with a few real messages. */
  planBenchmark: (target: string) => post((data) => data, "/v1/benchmarks/plan", { target }),
  /** Write the planned test set, ready to run. */
  approveBenchmarkPlan: (target: string) =>
    post((data) => data as { set: string; questions: number }, "/v1/benchmarks/plan/approve", {
      target,
    }),
  /** Write a size as a test set, downloading its data the first time. */
  installBenchmark: (target: string, size: string) =>
    post((data) => data as { set: string; questions: number }, "/v1/benchmarks/install", {
      target,
      size,
    }),
  library: (target: string) => get(many(LibraryRowSchema), `/v1/library${query({ target })}`),
  sets: (target: string) => get(many(SetSummarySchema), `/v1/targets/${enc(target)}/sets`),
  cases: ({ target, set }: Scope) =>
    get(many(CaseRowSchema), `/v1/evals/sets/${enc(set)}/cases${query({ target })}`),
  /** The server nests the file under `file`, beside its id and history. */
  caseFile: ({ target, set }: Scope, id: string) =>
    get(
      (data) => {
        const { file, ...rest } = (data ?? {}) as { file?: object };
        return CaseFileViewSchema.parse({ ...file, ...rest });
      },
      `/v1/evals/sets/${enc(set)}/cases/${enc(id)}${query({ target })}`,
    ),
  sources: (target: string) =>
    get(many(SourceSchema), `/v1/evals/sources${query({ target, limit: 20 })}`),
  /** The test's file as written, to edit by hand. */
  file: ({ target, set }: Scope, id: string) =>
    raw("GET", `/v1/evals/sets/${enc(set)}/cases/${enc(id)}/file${query({ target })}`),
  /** Saves the file as typed; refused, with why, when it isn't a valid test. */
  saveFile: ({ target, set }: Scope, id: string, text: string, reviewed?: string) =>
    raw(
      "PUT",
      `/v1/evals/sets/${enc(set)}/cases/${enc(id)}/file${query({ target })}`,
      text,
      reviewed ? { "X-Reviewed": reviewed } : {},
    ),
  passMarks: ({ target, set }: Scope, marks: PassMarks) =>
    call("PUT", `/v1/evals/sets/${enc(set)}/pass-marks${query({ target })}`, marks),
  runs: ({ target, set }: Scope) =>
    get(many(RunSummarySchema), `/v1/evals/runs${query({ target, set })}`),
  run: (run: string, base?: string | null) =>
    get(one(RunDetailSchema), `/v1/evals/runs/${enc(run)}${query({ base })}`),
  /** What a test checks, step by step, in plain words. */
  testPlan: ({ target, set }: Scope, id: string) =>
    get(
      one(TestPlanSchema),
      `/v1/evals/sets/${enc(set)}/cases/${enc(id)}/plan${query({ target })}`,
    ),
  /** The plan with try `n` of `run` laid over it (the first failed try when `n` is left out). */
  comparison: (run: string, base?: string | null) =>
    get(one(ComparisonSchema), `/v1/evals/runs/${enc(run)}/comparison${query({ base })}`),
  compare: (run: string, id: string, n?: number | null) =>
    get(
      one(CompareSchema),
      `/v1/evals/runs/${enc(run)}/cases/${enc(id)}/compare${query({ try: n })}`,
    ),
  /** Agree (`true`) or not with the grader on rubric line `line` of try `n`; `null` clears it. */
  label: (run: string, id: string, n: number, line: number, agree: boolean | null) => {
    const path = `/v1/evals/runs/${enc(run)}/cases/${enc(id)}/tries/${n}/rubric/${line}/label`;
    return (agree === null ? call("DELETE", path) : call("PUT", path, { agree })).then(
      one(LineAgreementSchema),
    );
  },
  estimate: ({ target, set }: Scope, id?: string, cases?: readonly string[], tries?: number) =>
    post(one(EstimateSchema), "/v1/evals/runs/estimate", {
      target,
      set,
      ...(id ? { case: id } : {}),
      ...(cases ? { cases } : {}),
      ...(tries ? { tries } : {}),
    }),
  /** Starts the run an estimate described, graded with the set's pass marks. */
  start: (token: string) => post(one(RunSummarySchema), "/v1/evals/runs", { token }),
  /** What the run's analysis said about one test, when it said something. */
  finding: (run: string, id: string) =>
    get(maybe(RunCaseSchema), `/v1/evals/runs/${enc(run)}/cases/${enc(id)}`).then(
      (found) => found?.finding ?? null,
      () => null,
    ),
  stop: (run: string) => post(one(RunSummarySchema), `/v1/evals/runs/${enc(run)}/stop`),
  analyze: (run: string) => post(one(RunSummarySchema), `/v1/evals/runs/${enc(run)}/analyze`),
  health: (target: string, period: Period) =>
    get(one(HealthSchema), `/v1/targets/${enc(target)}/health${query({ period })}`),
  understanding: (target: string) =>
    get(one(UnderstandingSchema), `/v1/targets/${enc(target)}/understanding`),
  /** Stores one steer; the answer is the understanding after it, when the server sends it. */
  steer: (target: string, steer: Steer): Promise<Understanding | null> =>
    post(maybe(UnderstandingSchema), `/v1/targets/${enc(target)}/understanding/steer`, steer),
  /** Brings what it knows up to date now: one light model call. */
  updateKnowledge: (target: string) =>
    post(one(UnderstandingSchema), `/v1/targets/${enc(target)}/understanding/update`),
  undoKnowledge: (target: string) =>
    post(one(UnderstandingSchema), `/v1/targets/${enc(target)}/understanding/undo`),
  sawKnowledge: (target: string) =>
    call("POST", `/v1/targets/${enc(target)}/understanding/seen`, {}),
  /** What the change on the card means: one light model call. */
  readChange: (target: string) => call("POST", `/v1/targets/${enc(target)}/change/read`, {}),
  updates: {
    get: (target: string) => get(one(UpdatesSchema), `/v1/targets/${enc(target)}/updates`),
    set: (target: string, mode: Knowledge["mode"]) =>
      call("PUT", `/v1/targets/${enc(target)}/updates`, { mode }).then(one(UpdatesSchema)),
  },
  hooks: {
    get: (target: string) => get(one(HooksSchema), `/v1/targets/${enc(target)}/hooks`),
    install: (target: string) => post(one(HooksSchema), `/v1/targets/${enc(target)}/hooks`),
    remove: (target: string) =>
      call("DELETE", `/v1/targets/${enc(target)}/hooks`).then(one(HooksSchema)),
  },
  /**
   * Asks the agent to plan these starters in `session` (else one the server opens on `set`); the
   * plans arrive in that session.
   */
  plan: (
    target: string,
    starters: string[],
    { session, set }: { session?: string; set?: string } = {},
  ): Promise<AgentSession | null> =>
    post(maybe(AgentSessionSchema), `/v1/targets/${enc(target)}/understanding/plan`, {
      starters,
      ...(session ? { session } : {}),
      ...(set ? { set } : {}),
    }),
  cardMarkdown: (target: string) =>
    call("GET", `/v1/targets/${enc(target)}/card.md`).then((data) => String(data)),
  model: {
    get: () => get(one(ModelSettingsSchema), "/v1/settings/model"),
    save: (body: ModelBody) =>
      call("PUT", "/v1/settings/model", body).then(one(ModelSettingsSchema)),
    /** The provider's models; a key typed but not saved goes in a header, never the URL. */
    list: (provider: string, base_url: string, key: string) =>
      call(
        "GET",
        `/v1/settings/model/models${query({ provider, base_url })}`,
        undefined,
        key ? { "X-Model-Key": key } : {},
      ).then(one(ModelListSchema)),
    /** One tiny call on each model: the form as it stands when given, else the saved one. */
    test: (body?: ModelBody) => post(one(ModelTestSchema), "/v1/settings/model/test", body ?? {}),
  },
  agent: {
    open: ({ target, set }: Scope) =>
      post(one(AgentSessionSchema), "/v1/evals/agent/sessions", { target, set }),
    session: (id: string) => get(one(AgentSessionSchema), `/v1/evals/agent/sessions/${enc(id)}`),
    chats: (target: string) =>
      get(many(AgentChatSchema), `/v1/evals/agent/sessions${query({ target })}`),
    send: (id: string, text: string, run?: string) =>
      post(one(AgentSessionSchema), `/v1/evals/agent/sessions/${enc(id)}/messages`, { text, run }),
    approve: (id: string, plan_id: string) =>
      post(one(AgentSessionSchema), `/v1/evals/agent/sessions/${enc(id)}/approve`, { plan_id }),
    /** Puts the suggestion flagged on rubric `line` in its place, in a plan not approved yet. */
    swapLine: (id: string, plan_id: string, line: string) =>
      post(
        one(AgentSessionSchema),
        `/v1/evals/agent/sessions/${enc(id)}/plans/${enc(plan_id)}/use`,
        { line },
      ),
    stop: (id: string) => post(one(AgentSessionSchema), `/v1/evals/agent/sessions/${enc(id)}/stop`),
  },
};
