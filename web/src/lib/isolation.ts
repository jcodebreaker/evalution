import type { Isolation, RunIsolation, StoreWords, TryIsolation } from "./schema";

/** How a run or try was isolated, in the words a result shows. */
export function isolatedWords(isolation: Pick<RunIsolation, "how" | "plan_version">): string {
  if (isolation.how === "proven")
    return isolation.plan_version === null
      ? "Isolated (proven plan)"
      : `Isolated (proven plan v${isolation.plan_version})`;
  if (isolation.how === "identity") return "Identity only";
  return "Not isolated";
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

/** A store as a person reads it: "Conversations (Postgres database atrium)". */
export function storeLabel(store: StoreWords): string {
  const where = `${storeKind(store.kind)} ${store.name}`;
  return store.holds ? `${store.holds} (${where})` : where;
}

/** The same inside a sentence: "conversations (Postgres database atrium)". */
function inSentence(store: StoreWords): string {
  const label = storeLabel(store);
  return store.holds ? label.charAt(0).toLowerCase() + label.slice(1) : label;
}

/** "a", "a and b", "a, b and c". */
export function joinWords(words: readonly string[]): string {
  if (words.length < 2) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** Seconds to a tenth, as a try's own time is said. */
const tenths = (seconds: number) => `${Math.round(seconds * 10) / 10} s`;

/** Rough seconds, as an estimate says them. */
export const about = (seconds: number) => `about ${Math.max(1, Math.round(seconds))} s`;

/** The stores `ids` names, in plain words; an id the plan no longer has shows as it is. */
function named(ids: readonly string[], stores: readonly StoreWords[] | null | undefined): string[] {
  return ids.map((id) => {
    const store = stores?.find((entry) => entry.id === id);
    return store ? inSentence(store) : id;
  });
}

/** What the check around a try found wrong, its store ids in plain words. */
function problemWords(problem: string, stores: readonly StoreWords[] | null | undefined): string {
  const held = "reset didn't hold: ";
  if (problem.startsWith(held)) {
    const rest = problem.slice(held.length);
    const ids = rest.split(", ").map((part) => part.trim());
    const known = ids.every((id) => stores?.some((store) => store.id === id));
    return `Putting it back didn't hold: ${known ? joinWords(named(ids, stores)) : rest}. This try isn't judged.`;
  }
  return problem.charAt(0).toUpperCase() + problem.slice(1);
}

/** A try's "Before" step: its title, what was done, and what went wrong when anything did. */
export type BeforeWords = {
  title: string;
  line: string | null;
  warning: string | null;
  /** It started clean, put back and checked; `null` when nothing says so. */
  clean: boolean | null;
};

const FRESH = "A fresh person";

/**
 * What a try started from, said in its "Before" step: put back and checked by a proven plan, a
 * new person only, or nothing. `title` is the server's own ("A fresh person, with the file
 * attached"); what it says of files is kept.
 */
export function beforeWords(isolation: TryIsolation, title: string | null): BeforeWords {
  const files = title?.startsWith(FRESH) ? title.slice(FRESH.length) : "";
  const warning = isolation.problem ? problemWords(isolation.problem, isolation.stores) : null;
  if (isolation.how === "proven") {
    const what = joinWords(named(isolation.checked, isolation.stores));
    const time =
      isolation.restore_seconds === null ? "" : ` in ${tenths(isolation.restore_seconds)}`;
    return {
      title: `${warning ? "Not started clean" : "Started clean"}${files}`,
      line: what ? `Put back ${what}${time}, and checked them.` : `Put back${time}.`,
      warning,
      clean: warning ? null : true,
    };
  }
  if (isolation.how === "identity") {
    return {
      title: `A new person${files}`,
      line: "A new person, with nothing of earlier tries'; what the app keeps elsewhere isn't checked.",
      warning,
      clean: null,
    };
  }
  return {
    title: `Not started clean${files}`,
    line: null,
    warning: ["Not isolated: this try may see what earlier ones left.", warning]
      .filter(Boolean)
      .join(" "),
    clean: null,
  };
}
