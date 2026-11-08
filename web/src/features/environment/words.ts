import type { Isolation, IsolationStep, StoreWords, TestSetup } from "../../lib/schema";

/** What a store is, short, after "New": "New Postgres", "New memory". */
const SHORT: Record<string, string> = {
  postgres: "Postgres",
  mysql: "MySQL",
  sqlite: "SQLite file",
  redis: "Redis",
  mongo: "MongoDB",
  vector: "vector index",
  dir: "folder",
  volume: "volume",
  hosted: "hosted service",
  memory: "memory",
};
const DATABASES = new Set(["postgres", "mysql", "sqlite", "redis", "mongo"]);

export const shortKind = (kind: string) => SHORT[kind] ?? kind;

/** How the app's own side names a store: "Your database", "Your memory". */
export const yourKind = (kind: string) =>
  DATABASES.has(kind)
    ? "Your database"
    : kind === "memory"
      ? "Your memory"
      : `Your ${shortKind(kind)}`;

/** The test copy's name, as evalution names its first one. */
export const copyName = (target: string) => `eval-${target}-0`;

/** Two letters for a face: "Alex Rivera" → "AR". */
export function initials(name: string): string {
  const letters = name
    .trim()
    .split(/\s+/)
    .map((word) => word[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return letters || "?";
}

/** Seconds as the checklist says them: "18 s". */
export const stepTime = (seconds: number) => `${Math.max(1, Math.round(seconds))} s`;

/** The steps before anything has run: what setting it up will do, all waiting. */
export function waitingSteps(target: string, stores: readonly StoreWords[]): IsolationStep[] {
  const step = (key: string, label: string): IsolationStep => ({
    key,
    label,
    state: "waiting",
    seconds: null,
    detail: null,
  });
  return [
    step("find", "Find where it keeps things"),
    step("copy", `Copy ${target}`),
    ...stores
      .filter((store) => store.reset)
      .map((store) => step(`store:${store.id}`, `Give it its own ${shortKind(store.kind)}`)),
    step("person", "Make a test person"),
    step("prove", "Check it resets cleanly"),
  ];
}

/** One block on the test copy. */
export type Block = {
  key: string;
  kind: "app" | "traces" | "store" | "person";
  /** The test person's initials, when it has a name. */
  face?: string;
  /** A store's kind, for its icon. */
  of?: string;
  title: string;
  sub: string;
};

/** What a store's block says: "New Postgres", "conversations · empty". */
export function storeBlock(
  id: string,
  stores: readonly StoreWords[],
  planKinds: ReadonlyMap<string, string>,
  start: TestSetup["start"]["kind"],
  label: string,
): Block {
  const store = stores.find((entry) => entry.id === id);
  const kind = store?.kind ?? planKinds.get(id);
  const what = store ? (store.holds ? store.holds.toLowerCase() : store.name) : null;
  const how = start === "seed" ? "sample data" : "empty";
  return {
    key: `store:${id}`,
    kind: "store",
    of: kind,
    title: kind ? `New ${shortKind(kind)}` : label || "New store",
    sub: what ? `${what} · ${how}` : how,
  };
}

/**
 * What the test person's block says: the name a person gave it, or, for an app that keeps no name
 * (it names its own), nothing made up: who names it and its time zone.
 */
export function personBlock(setup: TestSetup | null, target: string): Block {
  const fields = setup?.supports.person ?? [];
  const named = fields.includes("name") && !!setup?.person.name;
  const every = setup && !setup.person.new_every_try ? "same person every try" : "new every try";
  const zone = fields.includes("timezone") ? setup?.person.timezone : null;
  const parts = named
    ? ["test person", every]
    : [`${target} names its own`, ...(zone ? [zone] : []), ...(fields.length ? [every] : [])];
  return {
    key: "person",
    kind: "person",
    title: named ? (setup?.person.name ?? "") : "Test person",
    ...(named ? { face: initials(setup?.person.name ?? "") } : {}),
    sub: parts.join(" · "),
  };
}

/** Where the test copy stands, as the picture shows it. */
export type Standing = "running" | "broke" | "proven" | "stale" | "leaky" | "none";

export function standing(isolation: Isolation): Standing {
  const job = isolation.job;
  if (job?.state === "running") return "running";
  if (job?.state === "failed") return "broke";
  switch (isolation.status) {
    case "proven":
      return "proven";
    case "stale":
      return "stale";
    case "failed":
      return "leaky";
    default:
      return "none";
  }
}

/** A step is under way or finished: its block shows. */
export const begun = (step: IsolationStep | undefined) =>
  step !== undefined && (step.state === "running" || step.state === "done");
