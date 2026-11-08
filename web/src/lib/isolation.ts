import type { Isolation, RunIsolation, TryIsolation } from "./schema";

/** How a run or try was isolated, in the words a result shows. */
export function isolatedWords(isolation: Pick<RunIsolation, "how" | "plan_version">): string {
  if (isolation.how === "proven")
    return isolation.plan_version === null
      ? "Isolated (proven plan)"
      : `Isolated (proven plan v${isolation.plan_version})`;
  if (isolation.how === "identity") return "Identity only";
  return "Not isolated";
}

/** What was done for one try before it played, in a few words. */
export function tryIsolationDetail(isolation: TryIsolation): string[] {
  const out: string[] = [];
  if (isolation.restore_seconds !== null)
    out.push(`put back in ${Math.round(isolation.restore_seconds * 10) / 10} s`);
  if (isolation.checked.length)
    out.push(
      `checked ${isolation.checked.length} ${isolation.checked.length === 1 ? "place" : "places"}`,
    );
  return out;
}

const KINDS: Record<string, string> = {
  postgres: "Postgres database",
  mysql: "MySQL database",
  sqlite: "SQLite file",
  redis: "Redis",
  mongo: "MongoDB",
  vector: "Vector index",
  dir: "Folder",
  volume: "Docker volume",
  hosted: "Hosted service",
  memory: "The app's own memory",
};

/** A kind of store, as a person names it. */
export const storeKind = (kind: string) => KINDS[kind] ?? kind;

type Store = NonNullable<Isolation["plan"]>["stores"][number];

/** How a store is reset between tries, in plain words. */
export function storeHow(store: Store): string {
  const by = store.moved_by.length ? `, pointed there by ${store.moved_by.join(", ")}` : "";
  switch (store.strategy) {
    case "relocate":
      return store.kind === "memory"
        ? "Cleared by starting the app again"
        : `Starts from a fresh copy each try${by}`;
    case "snapshot":
      return "Kept, and put back after each try";
    case "identity":
      return `Not reset: each try uses its own ${store.keyed_by ?? "person"}`;
    default:
      return "Not reset: tests can see what earlier ones left here";
  }
}

/** Where the plan stands, in one sentence. */
export function planStatusWords(isolation: Isolation): string {
  switch (isolation.status) {
    case "proven":
      return `Proven${isolation.version === null ? "" : ` (plan v${isolation.version})`}: every test starts from the same state.`;
    case "stale":
      return `Out of date: a commit changed ${isolation.stale_files.join(", ") || "a file it rests on"}.`;
    case "failed":
      return "Not proven: the rehearsal found the app keeping things the plan doesn't reset.";
    case "draft":
      return "Drafted, not proven yet.";
    default:
      return "Not found yet.";
  }
}
