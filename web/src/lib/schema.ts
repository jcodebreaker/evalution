import * as z from "zod";

// The page's view of the server's JSON (docs/architecture.md
// "API and page"). Times are seconds. Parsing is lenient: fields a
// server does not send yet fall back to their defaults.

export const VerdictSchema = z.enum(["pass", "flaky", "fail", "unjudged"]);
export type Verdict = z.infer<typeof VerdictSchema>;

/** How one try came out. */
export const TryOutcomeSchema = z.enum(["passed", "failed", "unjudged"]);
export type TryOutcome = z.infer<typeof TryOutcomeSchema>;

/** What a pass rests on: a code check of the outcome, a model's reading alone, or neither. */
export const BasisSchema = z.enum(["checked", "model_only", "none"]).catch("none");

/** Where the model reads the fault to be. Its reading: it moves no number. */
export const SuspectSchema = z.enum(["app", "test", "unclear"]).catch("unclear");
export type Suspect = z.infer<typeof SuspectSchema>;

export const TargetSchema = z.object({
  name: z.string(),
  adapter: z.string().nullable().default(null),
  /** Subset of reset, state, sections, isolation, parallel, conversation_break, background. */
  capabilities: z.array(z.string()).default([]),
  /** What its isolation means here, in one sentence. */
  isolation_note: z.string().nullable().default(null),
  /** What a try can rely on finding nothing of from earlier ones. */
  starts_clean: z.array(z.string()).default([]),
  /** What the owner asks every new test to start clean of. */
  required_clean: z.array(z.string()).default([]),
  /** A test message passed on its current settings. */
  checked: z.boolean().default(false),
  /** Connected from the page, so the page may edit it. */
  managed: z.boolean().default(false),
});
export type Target = z.infer<typeof TargetSchema>;

/** A target as the page edits it. Secrets come back by key only, never by value. */
export const TargetDetailSchema = z.object({
  name: z.string(),
  adapter: z.string(),
  adapter_label: z.string().default(""),
  /** Why its adapter doesn't load, when it doesn't: none of its runs can start. */
  adapter_error: z.string().nullable().default(null),
  /** The last change of its adapter, since which results test something else. */
  adapter_change: z
    .object({ from: z.string(), to: z.string(), at: z.number() })
    .nullable()
    .default(null),
  options: z.record(z.string(), z.unknown()).default({}),
  phoenix: z
    .object({
      url: z.string(),
      project: z.string(),
      public_url: z.string().optional(),
    })
    .nullable()
    .default(null),
  code: z
    .object({
      path: z.string(),
      ignore: z.array(z.string()).default([]),
      include: z.array(z.string()).default([]),
      summarize: z.number().nullable().default(null),
      area_model: z.string().nullable().default(null),
    })
    .nullable()
    .default(null),
  secrets: z.array(z.string()).default([]),
  /** From a save: stored secrets that stayed behind, since a host changed. */
  secrets_dropped: z.array(z.string()).default([]),
  managed: z.boolean().default(false),
  /** How it plays through its own page (ADR 0010), its map in plain words; none: its API. */
  page: z
    .object({
      map: z.array(z.object({ what: z.string(), where: z.string() })).default([]),
      /** Why its `page:` block doesn't read. */
      error: z.string().nullable().default(null),
      /** Reached through its page alone (`adapter: page`): no API under it. */
      only: z.boolean().default(false),
    })
    .nullish(),
});
export type TargetDetail = z.infer<typeof TargetDetailSchema>;

const CheckPartSchema = z.object({ ok: z.boolean(), detail: z.string() });
export const TargetCheckSchema = z.object({
  adapter: CheckPartSchema,
  phoenix: CheckPartSchema.extend({
    sessions_seen: z.number().default(0),
    has_llm_spans: z.boolean().default(false),
  }),
  code: CheckPartSchema.extend({ files_matched: z.number().default(0) }),
});
export type TargetCheck = z.infer<typeof TargetCheckSchema>;

/** A kind of app evalution can connect, with what the form asks for it. */
export const AdapterSchema = z.object({
  name: z.string(),
  label: z.string(),
  options: z.array(z.string()).default([]),
  required: z.array(z.string()).default([]),
  secrets: z.array(z.string()).default([]),
});
export type Adapter = z.infer<typeof AdapterSchema>;

/** One real message through a saved target. */
export const ProbeSchema = z.object({
  ok: z.boolean(),
  reply: z.string().default(""),
  seconds: z.number().default(0),
  tools: z.number().default(0),
  spans: z.number().default(0),
  traces: z.string(),
  problems: z.array(z.string()).default([]),
  /** It works, but tests needing its traces can't be judged. */
  warnings: z.array(z.string()).default([]),
  next: z.string().nullable().default(null),
  can_redraft: z.boolean().default(false),
});
export type Probe = z.infer<typeof ProbeSchema>;

/** What the model tried after a failed message; `options` is the one that worked. */
export const RedraftSchema = z.object({
  ok: z.boolean(),
  options: z.record(z.string(), z.unknown()).nullable().default(null),
  tried: z.array(
    z.object({
      settings: z.unknown(),
      problems: z.array(z.string()).default([]),
      reply: z.string().default(""),
    }),
  ),
});
export type Redraft = z.infer<typeof RedraftSchema>;

/** A test of the library, with its fit for this app and what its owner is asked for. */
const TallySchema = z.object({ right: z.number(), judged: z.number() });

const BenchmarkPlanSchema = z.object({
  verdict: z.string(),
  how: z.string(),
  why: z.string(),
  measures: z.string(),
  warnings: z.array(z.string()).default([]),
  evidence: z.array(z.string()).default([]),
  boundary: z.string().optional(),
  size: z.string().optional(),
  approved: z.boolean().default(false),
  stale: z.boolean().default(false),
  set: z.string().optional(),
  questions: z.number().optional(),
  messages: z.number().optional(),
  hours: z.number().optional(),
  probe: z
    .object({
      ok: z.boolean(),
      answered: z.boolean().default(false),
      memory_readable: z.boolean().default(false),
      seconds_per_message: z.number().optional(),
      error: z.string().optional(),
    })
    .optional(),
});
export type BenchmarkPlan = z.infer<typeof BenchmarkPlanSchema>;

export const BenchmarksSchema = z.object({
  plan: BenchmarkPlanSchema.nullable().default(null),
  sizes: z.array(
    z.object({
      size: z.string(),
      set: z.string(),
      about: z.string(),
      questions: z.number().nullable(),
    }),
  ),
  runs: z.array(
    z.object({
      run: z.string(),
      set: z.string(),
      status: z.string().nullable(),
      started_at: z.number().nullable(),
      judge: z.string().nullable(),
      untested: z.boolean().default(false),
      plan: BenchmarkPlanSchema.nullable().default(null),
      happened: z.string().default(""),
      totals: z
        .object({
          tokens: z.number().nullable().default(null),
          median_seconds: z.number().nullable().default(null),
          models: z.array(z.string()).default([]),
        })
        .nullable()
        .default(null),
      current: z
        .object({
          name: z.string().default(""),
          stage: z.string().default(""),
          turn: z.number().optional(),
          turns: z.number().optional(),
        })
        .nullable()
        .default(null),
      report: z.object({
        accuracy: z.number().nullable(),
        right: z.number(),
        judged: z.number(),
        task_average: z.number().nullable(),
        kinds: z.record(z.string(), TallySchema),
        abstention: z.number().nullable(),
        admitted: z.number(),
        unanswerable: z.number(),
        unjudged: z.number(),
        waiting: z.number(),
        write_recall: z.number().nullable(),
        reached: z.number().nullable().default(null),
        memory: z.object({
          measured: z.number(),
          written: z.number(),
          committed: z.number(),
          used: z.number(),
          reach_measured: z.number().default(0),
          reached: z.number().default(0),
          compact_measured: z.number().default(0),
          compacted: z.number().default(0),
        }),
        range: z.tuple([z.number(), z.number()]).nullable().default(null),
        previous_accuracy: z.number().nullable().default(null),
        change_is_real: z.boolean().default(false),
        broke_off: z.number().default(0),
      }),
      questions: z.array(
        z.object({
          case: z.string(),
          kind: z.string(),
          unanswerable: z.boolean(),
          question: z.string(),
          verdict: z.string(),
          tie_break: z.boolean().default(false),
          leaning: z.string().nullable().default(null),
          prompts: z.array(z.number()).default([]),
          memory: z
            .object({
              committed: z.boolean().nullable(),
              written: z.boolean().nullable(),
              reached: z.boolean().nullable().default(null),
              compacted: z.boolean().nullable().default(null),
            })
            .nullable(),
          right_is: z.string(),
          known: z.string().default(""),
          reply: z.string(),
          reason: z.string(),
          error: z.string().nullable(),
          seconds: z.number().nullable(),
          tokens: z.number().nullable().default(null),
          messages: z.number().nullable().default(null),
          trace_url: z.string().nullable().default(null),
          models: z.array(z.string()).default([]),
          broke_off: z.boolean().default(false),
        }),
      ),
    }),
  ),
});
export type Benchmarks = z.infer<typeof BenchmarksSchema>;

export const LibraryRowSchema = z.object({
  id: z.string(),
  title: z.string(),
  checks: z.string().default(""),
  kind: z.string().nullable().default(null),
  asks: z.array(z.object({ name: z.string(), label: z.string() })).default([]),
  fit: z.object({ verdict: z.string(), label: z.string(), reason: z.string().default("") }),
});
export type LibraryRow = z.infer<typeof LibraryRowSchema>;

/** What a save changes in what a test checks. */
export const MeaningChangeSchema = z.object({
  what: z.string(),
  before: z.unknown(),
  after: z.unknown(),
});
export type MeaningChange = z.infer<typeof MeaningChangeSchema>;

/** A project folder being looked at: plain steps, questions left, and the proposed target. */
export const DiscoverySchema = z.object({
  id: z.string(),
  folder: z.string(),
  status: z.enum(["working", "ready", "failed"]),
  steps: z.array(z.object({ text: z.string(), state: z.enum(["doing", "done", "problem"]) })),
  questions: z.array(z.object({ key: z.string(), text: z.string(), hint: z.string() })),
  target: TargetDetailSchema.nullable().default(null),
  /** How to send the app's traces to evalution. */
  traces: z
    .object({
      traced: z.boolean(),
      text: z.string(),
      lines: z.string(),
      command: z.string().nullable().default(null),
      arriving: z.boolean().default(false),
    })
    .nullable()
    .default(null),
});
export type Discovery = z.infer<typeof DiscoverySchema>;

/** What the page sends to connect or edit a target; a secret left out is kept. */
export type TargetBody = {
  name: string;
  adapter: string;
  options: Record<string, unknown>;
  /** The app's own Phoenix, read too; `null` when evalution's own traces are all it has. */
  phoenix: { url: string; project: string } | null;
  code: {
    path: string;
    ignore: string[];
    include?: string[];
    summarize?: number | null;
    area_model?: string | null;
  } | null;
  secrets: Record<string, string>;
};

/** Where apps send traces (the OTLP endpoint's base), and whether they need a traces token. */
export const TracesSchema = z.object({
  url: z.string(),
  token: z.boolean(),
});

/** Something a model says: `verified` when its refs exist and its quotes occur in the evidence. */
export const ModelClaimSchema = z.object({
  text: z.string(),
  refs: z.array(z.string()).default([]),
  quotes: z.array(z.string()).default([]),
  verified: z.boolean().default(false),
});
export type ModelClaim = z.infer<typeof ModelClaimSchema>;

export const TryAnalysisSchema = z.object({
  summary: ModelClaimSchema,
  cause: ModelClaimSchema,
  fix: z.string(),
  suspected: SuspectSchema,
});
export type TryAnalysis = z.infer<typeof TryAnalysisSchema>;

export const RunAnalysisSchema = z.object({
  summary: ModelClaimSchema,
  patterns: z
    .array(
      z.object({
        title: z.string(),
        detail: ModelClaimSchema,
        cases: z.array(z.string()).default([]),
        suspected: SuspectSchema,
      }),
    )
    .default([]),
});
export type RunAnalysis = z.infer<typeof RunAnalysisSchema>;

export const TotalsSchema = z.object({
  passed: z.number(),
  passed_checked: z.number().default(0),
  passed_model_only: z.number().default(0),
  unjudged: z.number().default(0),
  cases: z.number(),
  /** Means of the tests that have something to score; `null` when none has. */
  tools: z.number().nullable(),
  reply: z.number().nullable(),
  median_seconds: z.number(),
  tokens: z.number(),
});
export type Totals = z.infer<typeof TotalsSchema>;

export const PassMarksSchema = z.object({
  tools: z.number(),
  reply: z.number(),
});
export type PassMarks = z.infer<typeof PassMarksSchema>;

/** How a try was isolated: by a proven plan, by its own person only, or not at all. */
export const IsolationHowSchema = z.enum(["proven", "identity", "none"]).catch("none");
export type IsolationHow = z.infer<typeof IsolationHowSchema>;

/** A place the app keeps things, as the page names it: "Conversations (Postgres database atrium)". */
export const StoreWordsSchema = z.object({
  id: z.string(),
  kind: z.string(),
  /** Its own name: the database, the volume, the file. */
  name: z.string(),
  /** What it holds, in a person's words, when the probe showed it. */
  holds: z.string().nullable().default(null),
  /** The plan puts it back before each try. */
  reset: z.boolean().default(true),
});
export type StoreWords = z.infer<typeof StoreWordsSchema>;

/** How one try was isolated, and what was checked before it. Older runs don't say. */
export const TryIsolationSchema = z.object({
  how: IsolationHowSchema,
  plan_version: z.number().nullable().default(null),
  restore_seconds: z.number().nullable().default(null),
  checked: z.array(z.string()).default([]),
  /** What the check before or after the try found wrong, in words. */
  problem: z.string().nullable().default(null),
  /** The plan's stores, to name the ids in `checked`; older servers don't send them. */
  stores: z.array(StoreWordsSchema).nullish(),
});
export type TryIsolation = z.infer<typeof TryIsolationSchema>;

/** How a run's tries were isolated. */
export const RunIsolationSchema = z.object({
  how: IsolationHowSchema,
  plan_version: z.number().nullable().default(null),
});
export type RunIsolation = z.infer<typeof RunIsolationSchema>;

/** Older servers' step states, in the words the page uses now. */
const OLD_STEP_STATES: Record<string, string> = { doing: "running", problem: "failed" };

/**
 * One step of setting up the test copy, in order: `find`, `copy`, `store:<id>`, `person`, `prove`.
 * Older servers send `{key, text, state: doing|done|problem}`; it reads the same.
 */
export const IsolationStepSchema = z.preprocess(
  (raw) => {
    if (!raw || typeof raw !== "object") return raw;
    const step = raw as Record<string, unknown>;
    const state = typeof step.state === "string" ? step.state : "";
    return { ...step, label: step.label ?? step.text, state: OLD_STEP_STATES[state] ?? state };
  },
  z.object({
    key: z.string(),
    label: z.string().default(""),
    state: z.enum(["waiting", "running", "done", "failed"]).catch("waiting"),
    /** How long it took, or has taken so far. */
    seconds: z.number().nullable().default(null),
    /** What went wrong, when it failed. */
    detail: z.string().nullable().default(null),
  }),
);
export type IsolationStep = z.infer<typeof IsolationStepSchema>;

/** Who each try talks as, what the copy starts with, and how often it is put back. */
export const TestSetupSchema = z.object({
  person: z
    .object({
      name: z.string().default(""),
      language: z.string().nullable().default(null),
      timezone: z.string().nullable().default(null),
      new_every_try: z.boolean().default(true),
    })
    .prefault({}),
  start: z
    .object({
      kind: z.enum(["empty", "seed"]).catch("empty"),
      file: z.string().nullable().default(null),
    })
    .prefault({ kind: "empty" }),
  /** Put back before every try, or before every test. */
  reset: z.enum(["try", "test"]).catch("try"),
  copies: z.number().int().default(1),
  version: z.number().default(0),
  /** What this app lets the page change: the person's fields, and starting from sample data. */
  supports: z
    .object({
      person: z.array(z.string()).default([]),
      seed: z.boolean().default(false),
    })
    .prefault({}),
  /** Changed since it was proven: it's checked again before the next run. */
  needs_check: z.boolean().default(false),
});
export type TestSetup = z.infer<typeof TestSetupSchema>;

/** A change to part of the test setup, as PUT takes it. */
export type TestSetupChange = {
  person?: Partial<TestSetup["person"]>;
  start?: Partial<TestSetup["start"]>;
  reset?: TestSetup["reset"];
  copies?: number;
};

/** A piece UI testing lacks here (ADR 0010), in one sentence, and what fixes it: a copy of the
 * app (Set up testing makes one), or the command that installs Playwright, a browser or ffmpeg. */
export const MissingPieceSchema = z.object({
  part: z.enum(["copy", "playwright", "browser", "ffmpeg"]).catch("browser"),
  said: z.string(),
  command: z.string().nullable().default(null),
});
export type MissingPiece = z.infer<typeof MissingPieceSchema>;

/** A job the page follows as it goes: its steps, each waiting, running, done or failed. */
const LiveJobSchema = z.object({
  state: z.enum(["running", "done", "failed"]).catch("failed"),
  started: z.number().default(0),
  ended: z.number().nullable().default(null),
  steps: z.array(IsolationStepSchema).default([]),
  error: z.string().nullable().default(null),
});

/** Installing what UI testing lacks on the server's computer: its steps and its last lines. */
export const InstallJobSchema = LiveJobSchema.extend({
  lines: z.array(z.string()).default([]),
});
export type InstallJob = z.infer<typeof InstallJobSchema>;

/** What UI testing has on this computer: ready, or each piece it lacks, and whether the page can
 * install them here (else `why_not_install` says how). */
export const UiTestingSchema = z.object({
  ready: z.boolean().default(false),
  missing: z.array(MissingPieceSchema).default([]),
  /** Each piece and whether it's here; older servers don't say. */
  parts: z
    .object({ playwright: z.boolean(), browser: z.boolean(), ffmpeg: z.boolean() })
    .nullable()
    .catch(null)
    .default(null),
  can_install: z.boolean().default(false),
  why_not_install: z.string().nullable().default(null),
  job: InstallJobSchema.nullable().catch(null).default(null),
});
export type UiTesting = z.infer<typeof UiTestingSchema>;

/** One entry of a page map in plain words: what it is, and where on the page. */
export const MapWordsSchema = z.object({ what: z.string(), where: z.string() });

/** A page map kept for a target (ADR 0010): in use, or tried on evalution's copy and waiting for
 * approval, with its rehearsal's screenshots. */
export const KeptPageMapSchema = z.object({
  status: z
    .enum(["tried", "approved", "stale", "broken", "by_hand"])
    .catch("approved")
    .default("approved"),
  map: z.array(MapWordsSchema).default([]),
  /** Why the map doesn't read, when it doesn't. */
  error: z.string().nullable().default(null),
  report: z.string().default(""),
  /** The rehearsal's screenshots, as URLs on this server. */
  shots: z.array(z.string()).default([]),
  /** The rehearsal's video, a URL on this server, when it kept one. */
  video: z.string().nullable().default(null),
  /** What the check before a run didn't find on the page. */
  missing: z.array(z.string()).default([]),
  /** The page as that check (or a try) found it, a screenshot URL on this server. */
  missing_shot: z.string().nullable().default(null),
  /** The files a commit changed under its evidence, when it's out of date. */
  stale_files: z.array(z.string()).default([]),
  updated: z.number().nullable().default(null),
  by_hand: z.boolean().default(false),
});
export type KeptPageMap = z.infer<typeof KeptPageMapSchema>;

/** How a test plays: through the app's API, its page (its API under it), or its page alone. */
export const PlaysSchema = z.enum(["api", "page", "page_only"]).catch("api").default("api");

/** When a try's screenshots or video are kept: every try, only one that fails, or never taken. */
export const KeepSchema = z.enum(["always", "failed", "off"]);
export type Keep = z.infer<typeof KeepSchema>;

/** What a try played through the page keeps. */
export const ProofSchema = z.object({ screenshots: KeepSchema, video: KeepSchema });
export type Proof = z.infer<typeof ProofSchema>;

/** A test's proof: the target's with the test's own (`own`) over it. */
export const TestProofSchema = ProofSchema.extend({
  own: z.object({ screenshots: KeepSchema.optional(), video: KeepSchema.optional() }).default({}),
  target: ProofSchema,
});
export type TestProof = z.infer<typeof TestProofSchema>;

/** A test's proof as saved. */
export const TestProofChangeSchema = z.object({
  plays: PlaysSchema,
  proof: TestProofSchema.nullable().default(null),
});

/** Whether to offer UI testing for a target: its code has a chat page, and nothing about its
 * page is set up, waiting or turned down. */
export const PageOfferSchema = z.object({
  target: z.string(),
  offer: z.boolean().default(false),
  /** Set up UI testing was pressed: the agent is learning the page, or its map waits for
   * Approve, which the card shows in place of the offer. */
  underway: z.boolean().default(false),
  /** The file the chat page was found in. */
  found_in: z.string().nullable().default(null),
  dismissed: z.boolean().default(false),
  /** The agent can learn the page now; else `why_not` says what comes first. */
  can_learn: z.boolean().default(false),
  why_not: z.string().nullable().default(null),
  /** What UI testing lacks, when that's why not, with what fixes it. */
  missing: MissingPieceSchema.nullable().catch(null).default(null),
});
export type PageOffer = z.infer<typeof PageOfferSchema>;

/** The agent learning a target's page from Set up UI testing, as the page follows it. */
export const PageLearnSchema = LiveJobSchema.extend({
  /** The writer session doing it, kept among the app's chats. */
  session: z.string().nullable().default(null),
});
export type PageLearn = z.infer<typeof PageLearnSchema>;

/** How tests reach a target: through its API, or through its page by a map the test writer
 * learned and a person approved once. */
export const PageReachSchema = z.object({
  target: z.string().default(""),
  plays: PlaysSchema,
  /** Connected from the page, so its page: block can be written here. */
  managed: z.boolean().default(false),
  /** Its map can be approved here: all but a target whose evalution.yaml block has a page: of
   * its own (one set there gets its approved map stored beside the file). */
  writable: z.boolean().default(false),
  /** What a try played through the page keeps; none through the API. */
  proof: ProofSchema.nullable().catch(null).default(null),
  current: KeptPageMapSchema.nullable().default(null),
  proposed: KeptPageMapSchema.nullable().default(null),
  history: z
    .array(z.object({ version: z.number(), status: z.string(), updated: z.number() }))
    .default([]),
  can_undo: z.boolean().default(false),
  /** The page can be opened on evalution's copy; else `why_not` says why, in words. */
  can_learn: z.boolean().default(false),
  why_not: z.string().nullable().default(null),
  /** What UI testing lacks, when that's why not, with what fixes it. */
  missing: MissingPieceSchema.nullable().catch(null).default(null),
  problem: z.string().nullable().default(null),
  /** The agent learning the page from Set up UI testing, while it does and after. */
  learn: PageLearnSchema.nullable().catch(null).default(null),
});
export type PageReach = z.infer<typeof PageReachSchema>;

/** One thing a build recipe rests on: `path:line` in the app's folder, and what it says there. */
export const BuildClaimSchema = z.object({ at: z.string(), says: z.string().default("") });

/** How a version of the app's code is built into evalution's own names (ADR 0011): through the
 * two variables its compose files name images with, an override evalution writes, or a process
 * app's setup commands. */
export const BuildRecipeSchema = z.object({
  kind: z.enum(["compose_env", "compose_override", "process"]).catch("compose_override"),
  /** The recipe in a sentence or two. */
  words: z.string().default(""),
  files: z.array(z.string()).default([]),
  services: z.array(z.string()).default([]),
  profiles: z.array(z.string()).default([]),
  name_var: z.string().nullable().default(null),
  tag_var: z.string().nullable().default(null),
  /** Each built service's image under evalution's name, `<commit>` for the version's tag. */
  images: z.record(z.string(), z.string()).default({}),
  /** Each built service's image as the app's own files name it. */
  yours: z.record(z.string(), z.string()).default({}),
  setup: z.array(z.object({ run: z.array(z.string()), cwd: z.string().default(".") })).default([]),
  evidence: z.array(BuildClaimSchema).default([]),
  report: z.string().default(""),
  /** `rule`, or `agent` when the test writer adjusted it. */
  by: z.string().default("rule"),
  why: z.string().default(""),
  updated: z.number().nullable().default(null),
});
export type BuildRecipe = z.infer<typeof BuildRecipeSchema>;

/** One version built into evalution's names: how long it took and the disk it took. */
export const BuiltVersionSchema = z.object({
  tag: z.string().default(""),
  images: z.array(z.string()).default([]),
  seconds: z.number().nullable().default(null),
  disk_bytes: z.number().nullable().default(null),
  image_bytes: z.number().nullable().default(null),
  at: z.number().nullable().default(null),
});
export type BuiltVersion = z.infer<typeof BuiltVersionSchema>;

/** Proving a recipe as it goes: its steps and the build's last lines. */
export const BuildJobSchema = LiveJobSchema.extend({
  lines: z.array(z.string()).default([]),
});
export type BuildJob = z.infer<typeof BuildJobSchema>;

/** A target's build recipe as the Test environment page shows it. */
export const BuildSchema = z.object({
  target: z.string(),
  status: z.enum(["none", "proposed", "proven", "stale", "failed"]).catch("none"),
  version: z.number().nullable().default(null),
  recipe: BuildRecipeSchema.nullable().default(null),
  last_build: BuiltVersionSchema.nullable().catch(null).default(null),
  built: z.array(BuiltVersionSchema).default([]),
  /** The files it rests on that changed since it was proposed. */
  stale_files: z.array(z.string()).default([]),
  can_undo: z.boolean().default(false),
  can_propose: z.boolean().default(false),
  can_prove: z.boolean().default(false),
  /** What proving costs, said before it starts: "builds relay-lite's code once: …". */
  cost: z.string().default(""),
  free_bytes: z.number().default(0),
  min_free_bytes: z.number().default(5 * 1024 ** 3),
  job: BuildJobSchema.nullable().catch(null).default(null),
  problem: z.string().nullable().default(null),
  /** What the last removal took away, and whether the Docker VM's disk was trimmed. */
  removed: z
    .object({
      images: z.array(z.string()).default([]),
      missing: z.array(z.string()).default([]),
      trimmed: z.boolean().nullable().default(null),
      note: z.string().default(""),
    })
    .nullable()
    .catch(null)
    .default(null),
});
export type Build = z.infer<typeof BuildSchema>;

/** A target's isolation plan (ADR 0009): where the app keeps what it remembers, how each place
 * is reset between tries, and how far that is proven. */
export const IsolationSchema = z.object({
  target: z.string(),
  status: z.enum(["none", "draft", "proven", "stale", "failed"]).catch("none"),
  version: z.number().nullable().default(null),
  plan: z
    .object({
      stores: z
        .array(
          z.object({
            id: z.string(),
            kind: z.string(),
            locator: z.string(),
            strategy: z.enum(["relocate", "snapshot", "identity", "none"]).catch("none"),
            moved_by: z.array(z.string()).default([]),
            keyed_by: z.string().nullable().default(null),
            /** The probe saw the app use it. */
            seen: z.boolean().default(false),
          }),
        )
        .default([]),
      /** Found holding the probe and left alone: traces, kept as evidence. */
      exclude: z.array(z.string()).default([]),
      report: z.string().default(""),
      /** Where the last rehearsal found the probe, or a write, the plan doesn't cover. */
      leaks: z.array(z.string()).default([]),
      updated: z.number().nullable().default(null),
    })
    .nullable()
    .default(null),
  /** Files its evidence cites that a commit changed. */
  stale_files: z.array(z.string()).default([]),
  evidence: z
    .object({
      candidates: z.number(),
      touched: z.number(),
      found: z.array(z.string()).default([]),
      watched: z.array(z.string()).default([]),
    })
    .nullable()
    .default(null),
  history: z
    .array(
      z.object({
        version: z.number(),
        status: z.string(),
        updated: z.number(),
        stores: z.number(),
      }),
    )
    .default([]),
  can_undo: z.boolean().default(false),
  can_run: z.boolean().default(false),
  job: z
    .object({
      state: z.enum(["running", "done", "failed"]),
      started: z.number(),
      ended: z.number().nullable().default(null),
      steps: z.array(IsolationStepSchema).default([]),
      error: z.string().nullable().default(null),
    })
    .nullable()
    .default(null),
  problem: z.string().nullable().default(null),
});
export type Isolation = z.infer<typeof IsolationSchema>;

/** How one test of a run went. */
export const RunTestSchema = z.object({
  case: z.string(),
  name: z.string(),
  verdict: VerdictSchema.nullable(),
  /** How it came out, in the one word the server read from its checks. */
  label: z.string().nullable().default(null),
  basis: BasisSchema.default("none"),
  /** What its verdict leaves out, such as tries that showed no sign of the agent working. */
  note: z.string().nullable().default(null),
  /** Why it wasn't played, in plain words. */
  reason: z.string().nullable().default(null),
  /** Why tries couldn't be judged: see `unjudgedWords`. */
  unjudged: z.array(z.string()).default([]),
  /** The verdict it had when it was played, when today's rules say otherwise. */
  originally: VerdictSchema.nullable().default(null),
  passed: z.number(),
  different: z.number().default(0),
  tries: z.number(),
});
export type RunTest = z.infer<typeof RunTestSchema>;

/** Where a run played: by a hosted server, or by `evalution run` and uploaded; and its name. */
export const EnvironmentSchema = z.object({
  where: z.enum(["hosted", "local"]),
  name: z.string().nullable().default(null),
});
export type Environment = z.infer<typeof EnvironmentSchema>;

/** The code a run played (ADR 0011): its branch and commit, whether uncommitted changes were in
 * it, and whether evalution can vouch that this is what played; older servers don't say. */
export const VersionSchema = z.object({
  ref: z.string().default("disk"),
  branch: z.string().nullable().default(null),
  commit: z.string().nullable().default(null),
  dirty: z.boolean().nullable().default(null),
  /** `false`: what played may not be this code, and `why_unverified` says why. `null`: can't tell. */
  verified: z.boolean().nullable().default(null),
  why_unverified: z.string().nullable().default(null),
  /** In words: "what was on disk: page-testing @ e535c4d, with uncommitted changes", or a version
   * evalution checked out and built: "feature-x @ a1b2c3d". */
  said: z.string().default(""),
  /** Before a run of a version other than the disk: what checking it out and building it costs,
   * "Checks out feature-x @ a1b2c3d and builds it: about 4 min and up to 20 GB, once", or "Built
   * already". */
  cost: z.string().nullable().default(null),
  /** What was said of a version beside it: the reset plan or page map made on another version,
   * a compose file read from the person's checkout. */
  notes: z.array(z.string()).default([]),
  /** What actually played: on a compose copy each service's image (`kind: "images"`), on a
   * process copy the working tree (`kind: "tree"`); `null` on the person's own app, or a run
   * from before it was kept. */
  played: z
    .object({
      kind: z.string(),
      images: z
        .array(
          z.object({
            service: z.string().default(""),
            image: z.string(),
            /** The app's own files build it, as against an image pulled from elsewhere. */
            built: z.boolean().default(false),
          }),
        )
        .default([]),
    })
    .nullable()
    .catch(null)
    .default(null),
});
export type Version = z.infer<typeof VersionSchema>;

/** A branch, remote branch or tag of the app's repository (ADR 0011). */
export const VersionRefSchema = z.object({
  name: z.string(),
  ref: z.string(),
  kind: z.enum(["local", "remote", "fetched", "tag"]).catch("local"),
  commit: z.string(),
  subject: z.string().default(""),
  date: z.number().nullable().default(null),
  checked_out: z.boolean().default(false),
  worktree: z.boolean().default(false),
});
export type VersionRef = z.infer<typeof VersionRefSchema>;

/** The app's repository: its refs, which branch is checked out, its remotes. */
export const VersionsSchema = z.object({
  target: z.string(),
  remotes: z.array(z.string()).default([]),
  checked_out: z
    .object({ branch: z.string().nullable().default(null), commit: z.string().nullable() })
    .nullable()
    .default(null),
  refs: z.array(VersionRefSchema).default([]),
});
export type Versions = z.infer<typeof VersionsSchema>;

/** A remote's branches as `git ls-remote` lists them now: maybe not fetched here. */
export const RemoteBranchesSchema = z.object({
  remote: z.string(),
  branches: z
    .array(
      z.object({
        remote: z.string(),
        name: z.string(),
        ref: z.string(),
        commit: z.string(),
        fetched: z.boolean().default(false),
      }),
    )
    .default([]),
});
export type RemoteBranches = z.infer<typeof RemoteBranchesSchema>;

/** A remote's branch fetched into evalution's own refs, and its commit. */
export const FetchedBranchSchema = z.object({
  remote: z.string(),
  branch: z.string(),
  commit: z.string(),
});

export const RunSummarySchema = z.object({
  run: z.string(),
  target: z.string().nullable().default(null),
  set: z.string(),
  started_at: z.number(),
  /** When it ended; `null` while it goes, or from an older server. */
  ended_at: z.number().nullable().catch(null).default(null),
  environment: EnvironmentSchema.default({ where: "local", name: null }),
  status: z.enum(["running", "done", "stopped", "failed"]),
  /** Why a failed run stopped, as a kind (`version_not_built`, `app_not_ready`, ...) and in words;
   * older servers don't say. */
  failure: z.string().nullable().catch(null).default(null),
  stop_reason: z.string().nullable().catch(null).default(null),
  /** The server restarted under it, which says nothing of the app. */
  interrupted: z.boolean().default(false),
  /** Short hash of the app's prompt, when the target knows one. */
  prompt: z.string().default(""),
  pass_marks: PassMarksSchema,
  totals: TotalsSchema,
  analyzing: z.boolean().default(false),
  /** Why the run's analysis didn't finish; it can be tried again. */
  analysis_error: z.string().nullable().default(null),
  /** The tests this run plays, and whether that is all of the set. */
  cases: z.array(z.string()).default([]),
  scope: z.enum(["all", "some"]).default("all"),
  tests: z.array(RunTestSchema).default([]),
  /** How its tries were isolated; older runs don't say. */
  isolation: RunIsolationSchema.nullish(),
  /** The code it played; `null` before it knew, or from an older server. */
  version: VersionSchema.nullable().catch(null).default(null),
  /** How it reached the app: its API, its page beside it, or its page alone; older runs don't
   * say. */
  plays: z.enum(["api", "page", "page_only"]).nullable().catch(null).default(null),
  progress: z
    .object({
      done: z.number(),
      total: z.number(),
      tests_done: z.number().nullable().default(null),
      tests_total: z.number().nullable().default(null),
      current: z
        .object({
          case: z.string(),
          name: z.string(),
          try: z.number(),
          turn: z.number().nullish(),
          turns: z.number().nullish(),
          /** `playing`, `reading traces` or `grading`. */
          stage: z.string().nullish(),
        })
        .nullable(),
    })
    .default({
      done: 0,
      total: 0,
      tests_done: null,
      tests_total: null,
      current: null,
    }),
});
export type RunSummary = z.infer<typeof RunSummarySchema>;

export const SetSummarySchema = z.object({
  set: z.string(),
  name: z.string(),
  about: z.string().nullable().default(null),
  cases: z.number(),
  latest_run: RunSummarySchema.nullable().default(null),
  /** The scores a try must reach to pass, set for the whole set. */
  pass_marks: PassMarksSchema.nullable().default(null),
});
export type SetSummary = z.infer<typeof SetSummarySchema>;

export const CaseRowSchema = z.object({
  case: z.string(),
  name: z.string(),
  about: z.string().nullable().default(null),
  source: z.string().nullable().default(null),
  verdict: VerdictSchema.nullable(),
  label: z.string().nullable().default(null),
  basis: BasisSchema.default("none"),
  note: z.string().nullable().default(null),
  tries: z.array(TryOutcomeSchema).default([]),
  /** Each try's label, and whether it reached the assistant, in the order of `tries`. */
  labels: z.array(z.string().nullable()).default([]),
  called: z.array(z.boolean()).default([]),
  unjudged: z.array(z.string()).default([]),
  originally: VerdictSchema.nullable().default(null),
  /** Tries that got a good result by another route than expected. */
  different: z.number().default(0),
  reasons: z.array(z.string().nullable()).default([]),
  tools: z.number().nullable().default(null),
  reply: z.number().nullable().default(null),
  seconds: z.number().nullable().default(null),
  tokens: z.number().nullable().default(null),
  /** The run the latest result comes from, and when it started. */
  last_run: z.string().nullable().default(null),
  last_at: z.number().nullable().default(null),
  /** Why the test may be out of date with its target, from the target's drift. */
  stale: z.string().nullable().default(null),
  /** How it plays: through the app's page, or its API. */
  plays: z.enum(["api", "page"]).catch("api").default("api"),
  /** Which layer held on the try its page opens on (the first that didn't pass); older servers
   * don't say. */
  layers: z
    .lazy(() => LayersSchema)
    .nullable()
    .catch(null)
    .default(null),
});
export type CaseRow = z.infer<typeof CaseRowSchema>;

/** A run with its results. Its `cases` are rows here, where a summary lists ids. */
export const RunDetailSchema = RunSummarySchema.omit({ cases: true }).extend({
  cases: z.array(CaseRowSchema),
  base: RunSummarySchema.nullable().default(null),
  delta: z
    .object({
      better: z.number(),
      worse: z.number(),
      /** Tests that can't be compared: the test or the rules changed. */
      changed: z.number().default(0),
      tools: z.number(),
      reply: z.number(),
      median_seconds_pct: z.number(),
    })
    .nullable()
    .default(null),
  history: z
    .array(
      z.object({
        run: z.string(),
        prompt: z.string().default(""),
        verdicts: z.record(z.string(), VerdictSchema.nullable()),
      }),
    )
    .default([]),
  analysis: RunAnalysisSchema.nullable().default(null),
});
export type RunDetail = z.infer<typeof RunDetailSchema>;

export const SpanSchema = z.object({
  id: z.string(),
  parent: z.string().nullable(),
  kind: z.enum(["agent", "tool", "model", "missing"]),
  name: z.string(),
  start: z.number(),
  seconds: z.number(),
  model: z.string().optional(),
  tokens_in: z.number().optional(),
  tokens_out: z.number().optional(),
  input: z.string().optional(),
  output: z.string().optional(),
  check: z.string().optional(),
  /** The app called it by itself, and it wasn't scored. */
  automatic: z.boolean().optional(),
  /** Work a helper did, not the app's main agent. */
  helper: z.boolean().optional(),
});
export type Span = z.infer<typeof SpanSchema>;

/** One model call of a try: what the model was sent, by section, in tokens. */
export const ContextCallSchema = z.object({
  n: z.number(),
  span: z.string().nullable(),
  model: z.string(),
  tokens_in: z.number(),
  sections: z.array(
    z.object({
      /** Named by the target's adapter (system, memory, tools, turns, …). */
      name: z.string(),
      tokens: z.number(),
      preview: z.string().optional(),
    }),
  ),
});
export type ContextCall = z.infer<typeof ContextCallSchema>;

export const TrajectoryStepSchema = z.object({
  t: z.number(),
  actor: z.string(),
  kind: z.string(),
  text: z.string(),
  span: z.string().nullable(),
  automatic: z.boolean().optional(),
});
export type TrajectoryStep = z.infer<typeof TrajectoryStepSchema>;

/** A test as its file says it, with every result it has had: opens a test never run. */
export const CaseFileViewSchema = z.object({
  case: z.string().optional(),
  name: z.string(),
  about: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  turns: z.array(z.record(z.string(), z.unknown())).default([]),
  tools: z.record(z.string(), z.unknown()).default({}),
  reply: z
    .object({
      reference: z.string().default(""),
      rubric: z.array(z.string()).default([]),
      /** Rubric lines that must pass; every line when absent. */
      pass: z.number().optional(),
    })
    .default({ reference: "", rubric: [] }),
  history: z
    .array(
      z.object({
        run: z.string(),
        at: z.number(),
        verdict: VerdictSchema.nullable(),
      }),
    )
    .default([]),
  /** How it plays: through the app's API, its page (its API under it), or its page alone. */
  plays: PlaysSchema,
  /** What a try played through the page keeps; none through the API (nothing to capture). */
  proof: TestProofSchema.nullable().default(null),
});
export type CaseFileView = z.infer<typeof CaseFileViewSchema>;

/** What happened to one check, or one step, in a try. */
export const ActualSchema = z.object({
  passed: z.boolean(),
  /** Nobody could say: neither held nor failed. */
  unjudged: z.boolean().default(false),
  text: z.string().default(""),
  calls: z.array(z.string()).default([]),
  /** The spans that show it. */
  spans: z.array(z.string()).default([]),
  /** What the grader quoted for a line; `verified`: code found it in the evidence. */
  quotes: z.array(z.object({ text: z.string(), verified: z.boolean().nullable() })).default([]),
  /** What the app said back to this message. */
  reply: z.string().optional(),
});
export type Actual = z.infer<typeof ActualSchema>;

/** One check of a test, in plain words: must happen, must never happen, or allowed. */
export const PlanItemSchema = z.object({
  id: z.string(),
  text: z.string(),
  detail: z.string().optional(),
  mode: z.enum(["must", "never", "allowed"]),
  /** Decided by code from the calls, by the Grader from the steps, or by the Grader. */
  judged: z.enum(["exact", "steps", "graded"]),
  actual: ActualSchema.optional(),
});
export type PlanItem = z.infer<typeof PlanItemSchema>;

/** How a page check came out, decided by code from the page (ADR 0010). */
const PageCheckSchema = z.object({ outcome: TryOutcomeSchema, why: z.string().default("") });

/** What the app's page showed of one message, on a try played through it (ADR 0010). */
export const StepPageSchema = z.object({
  /** The page's text of the reply; `null`: it never showed. */
  shown: z.string().nullable().default(null),
  /** Seconds from the try's first message when it showed. */
  shown_at: z.number().nullable().default(null),
  /** What the page showed instead, when the reply didn't show. */
  instead: z.string().default(""),
  shot: z.string().nullable().default(null),
  /** Where the screenshot is read; `null` when this server doesn't hold it. */
  shot_url: z.string().nullable().default(null),
  /** The `shown` check of this message. */
  check: PageCheckSchema.nullable().default(null),
  /** How far the page showed a reply it failed to show: its words that showed, and the reply's
   * words from the first that didn't. */
  part: z.object({ shown: z.string(), missing: z.string() }).nullable().catch(null).default(null),
});
export type StepPage = z.infer<typeof StepPageSchema>;

export const PlanStepSchema = z.object({
  kind: z.enum(["before", "say", "filler", "break", "state", "agent", "reply", "passes"]),
  title: z.string().default(""),
  items: z.array(PlanItemSchema).default([]),
  files: z
    .array(
      z.object({
        name: z.string(),
        rows: z.number().optional(),
        columns: z.array(z.string()).optional(),
        peek: z.string().optional(),
        more: z.number().optional(),
      }),
    )
    .default([]),
  message: z.string().optional(),
  note: z.string().optional(),
  reference: z.string().optional(),
  actual: ActualSchema.optional(),
  /** What the page showed of this message; none on a try through the API. */
  page: StepPageSchema.nullish(),
});
export type PlanStep = z.infer<typeof PlanStepSchema>;

/** What a test checks, step by step; no run needed. */
export const TestPlanSchema = z.object({
  name: z.string(),
  about: z.string().nullable().optional(),
  steps: z.array(PlanStepSchema).default([]),
  limits: z.array(z.string()).default([]),
  /** Tool names in plain words. */
  names: z.record(z.string(), z.string()).default({}),
  /** The plain words are still being written; these are mechanical. */
  wording: z.boolean().default(false),
});
export type TestPlan = z.infer<typeof TestPlanSchema>;

export const LaneStepSchema = z.object({
  label: z.string(),
  status: z.enum(["ok", "missing", "extra", "wrong", "unjudged", "automatic"]),
  /** The call's span, when it was made. */
  span: z.string().optional(),
});
export type LaneStep = z.infer<typeof LaneStepSchema>;

const CheckRowSchema = z.object({
  id: z.string(),
  /** Its check kind (`state`, `shown`, `page_health`, ...); older servers don't say. */
  kind: z.string().nullish(),
  text: z.string(),
  outcome: TryOutcomeSchema,
  /** Recorded, and doesn't decide. */
  guide: z.boolean(),
});
export type CheckRow = z.infer<typeof CheckRowSchema>;

/** How a try's checks came out: what code decided, apart from what a model graded. */
export const ExplainSchema = z.object({
  code: z.object({ held: z.number(), total: z.number(), checks: z.array(CheckRowSchema) }),
  model: z.object({
    held: z.number(),
    total: z.number(),
    unjudged: z.number(),
    /** The lines against how many were needed, when not all were. */
    summary: z.string().nullable(),
    reference: CheckRowSchema.nullable(),
    /** Characters of the evidence the grader wasn't shown. */
    cut: z.number(),
  }),
  judge: z.string().nullable(),
  rules: z.number(),
  rules_from: z.number().nullable(),
});
export type Explain = z.infer<typeof ExplainSchema>;

/** An expected call against the call made. */
export const ToolRowSchema = z.object({
  expected: z.string().nullable(),
  actual: z.string().nullable(),
  status: z.string(),
  reason: z.string().nullable(),
  span: z.string().nullable(),
});
export type ToolRow = z.infer<typeof ToolRowSchema>;

const DivergedTrySchema = z.object({ run: z.string(), n: z.number(), span: z.string().nullable() });

/** Where a failing try first went another way than a passing one; found by code. */
export const DivergenceSchema = z.object({
  pass: DivergedTrySchema,
  fail: DivergedTrySchema,
  /** Of the try's tool calls in order, then its reply. */
  step: z.number(),
  kind: z.enum(["missing", "tool", "args", "context"]),
  detail: z.string(),
});
export type Divergence = z.infer<typeof DivergenceSchema>;
/** How often the grader matched a person on a rubric line, and their answer on the try shown. */
export const LineAgreementSchema = z.object({
  agreed: z.number(),
  labelled: z.number(),
  /** Too few agreed: the grader's passes on this line don't count. */
  unreliable: z.boolean(),
  /** They agree with what the grader says on this try; `null`: no answer. */
  mine: z.boolean().nullable().default(null),
  /** The grader decided the line on this try, so it can be answered. */
  can: z.boolean().default(false),
});
export type LineAgreement = z.infer<typeof LineAgreementSchema>;

/** The plan with one try of a run laid over it. */
const ComparedSideSchema = z
  .object({ verdict: z.string(), passed: z.number(), judged: z.number() })
  .nullable();

const ComparedVersionSchema = z.object({ name: z.string(), said: z.string().default("") });
const ComparedVersionsSchema = z.object({
  now: ComparedVersionSchema,
  before: ComparedVersionSchema,
  heading: z.string().nullable().default(null),
});
export const BaseBranchSchema = z.object({
  name: z.string(),
  run: z.string().nullable().default(null),
  missing: z.string().nullable().default(null),
});
export type BaseBranch = z.infer<typeof BaseBranchSchema>;

/** One run against another (GET /v1/evals/runs/{run}/comparison). */
export const ComparisonSchema = z.object({
  run: z.string(),
  base: z.string().nullable().default(null),
  answer: z.enum(["worse", "mixed", "better", "same"]).catch("same"),
  headline: z.string().default(""),
  could_be_chance: z.boolean().default(false),
  changed: z.array(z.object({ what: z.string(), before: z.string(), now: z.string() })).default([]),
  /** When what played says otherwise than the commits: "Same code played: …". */
  code: z.string().nullable().default(null),
  /** Each run's version, named and described, and "feature-x vs main" when they differ (ADR
   * 0011); older servers don't say. */
  versions: ComparedVersionsSchema.nullable().catch(null).default(null),
  /** For a run of a branch other than the repository's base branch: that branch, its latest run
   * of the same tests, and why there is none ("No run of these tests on main yet"). */
  base_branch: BaseBranchSchema.nullable().catch(null).default(null),
  tests: z
    .array(
      z.object({
        case: z.string(),
        name: z.string(),
        change: z.string(),
        chance: z.boolean().default(false),
        caveats: z.array(z.string()).default([]),
        before: ComparedSideSchema,
        now: ComparedSideSchema,
      }),
    )
    .default([]),
});
export type Comparison = z.infer<typeof ComparisonSchema>;

/** How one layer of a try came out: stored, answered or shown (ADR 0010). */
const LayerSchema = z.enum(["pass", "fail", "unjudged", "absent"]).catch("absent");
/** Which layer of the try shown held, from its check kinds; older servers don't say. */
export const LayersSchema = z.object({
  stored: LayerSchema,
  answered: LayerSchema,
  shown: LayerSchema,
  /** The page's own health, apart from what showed of the replies; only a run's rows say it. */
  page_health: LayerSchema.optional(),
});
export type Layers = z.infer<typeof LayersSchema>;

/** What the app's page showed over a whole try played through it (ADR 0010). */
export const TryPageSchema = z.object({
  console: z.array(z.object({ level: z.string(), text: z.string(), at: z.number() })).default([]),
  failed_requests: z
    .array(
      z.object({
        method: z.string(),
        path: z.string(),
        /** `null`: no answer came. */
        status: z.number().nullable().default(null),
        at: z.number(),
      }),
    )
    .default([]),
  /** Console lines and failed requests past the cap, not kept. */
  left_out: z.number().default(0),
  /** The `page_health` check. */
  health: PageCheckSchema.nullable().default(null),
  /** The shown layer with the page's health left out: what showed of the replies alone. */
  shown_alone: LayerSchema.nullable().catch(null).default(null),
  /** How the try comes out on every check but the page's; none when it had no API to play. */
  without_page: TryOutcomeSchema.nullable().catch(null).default(null),
  /** Reached through its page alone: each reply is what the page showed (reply only). */
  page_only: z.boolean().default(false),
  /** The app refused the page's message (its status), or never took it (`status: null`). */
  not_taken: z
    .object({ status: z.number().nullable().default(null) })
    .nullable()
    .catch(null)
    .default(null),
  video: z.object({ name: z.string(), url: z.string().nullable() }).nullable().default(null),
  /** Why there is no video when one was to be kept. */
  video_missing: z.string().nullable().default(null),
  /** What installs ffmpeg, when that's why there is no video and it's still missing here. */
  video_fix: MissingPieceSchema.nullable().catch(null).default(null),
  /** The page map ended the try: what the page lacked, and its screenshot of the page then. */
  broken_map: z
    .object({ missing: z.string(), shot_url: z.string().nullable().default(null) })
    .nullable()
    .catch(null)
    .default(null),
  /** Why the try shows no screenshots, when its proof said so (off, or kept only on failure). */
  shots_missing: z.string().nullable().default(null),
  /** The try was cut short before the page was closed. */
  interrupted: z.boolean().default(false),
});
export type TryPage = z.infer<typeof TryPageSchema>;

export const CompareSchema = TestPlanSchema.extend({
  run: z.string(),
  verdict: VerdictSchema.nullable().default(null),
  divergence: DivergenceSchema.nullable().catch(null).default(null),
  layers: LayersSchema.nullish(),
  /** What the app's page showed; none for a try played through the API alone. */
  page: TryPageSchema.nullish(),
  tries: z
    .array(
      z.object({
        n: z.number(),
        outcome: TryOutcomeSchema,
        reason: z.string().nullable(),
        label: z.string().nullable().default(null),
      }),
    )
    .default([]),
  try: z.number().nullable().default(null),
  passed: z.boolean().default(false),
  outcome: TryOutcomeSchema.default("failed"),
  label: z.string().nullable().default(null),
  /** Whether the try reached the assistant at all. */
  called: z.boolean().default(true),
  /** What had to be tried a second time, and why. */
  retried: z.array(z.object({ what: z.enum(["app", "grader"]), why: z.string() })).default([]),
  basis: BasisSchema.default("none"),
  different_route: z.boolean().default(false),
  reason: z.string().nullable().default(null),
  trace_url: z.string().nullable().default(null),
  seconds: z.number().nullable().default(null),
  /** What's missing of the try's traces, when any is. */
  trace_note: z.string().nullable().default(null),
  /** How this try was isolated; older runs don't say. */
  isolation: TryIsolationSchema.nullish(),
  explain: ExplainSchema.optional(),
  /** By rubric item id (`rubric-0`). */
  agreement: z.record(z.string(), LineAgreementSchema).default({}),
  tool_rows: z.array(ToolRowSchema).default([]),
  trajectory: z
    .object({
      expected: z.array(z.string()),
      tries: z.array(
        z.object({
          n: z.number(),
          outcome: TryOutcomeSchema,
          steps: z.array(LaneStepSchema),
        }),
      ),
    })
    .optional(),
  output: z
    .object({
      reply: z.string(),
      reference: z.string(),
      /** What the test wants of the reply, line by line. */
      must: z.array(z.string()).default([]),
      highlights: z.array(z.object({ text: z.string(), good: z.boolean(), item: z.string() })),
    })
    .optional(),
  detail: z
    .object({
      timeline: z.array(SpanSchema).default([]),
      context: z.array(ContextCallSchema).default([]),
      trajectory: z.array(TrajectoryStepSchema).default([]),
      analysis: TryAnalysisSchema.nullable().default(null),
    })
    .optional(),
});
export type Compare = z.infer<typeof CompareSchema>;

export const EstimateSchema = z.object({
  token: z.string(),
  cases: z.number(),
  tries: z.number(),
  chats: z.number(),
  /** Tests this app can't serve, left out of the run. */
  refused: z.array(z.object({ case: z.string(), why: z.string() })).default([]),
  /** How its tests play, what a try keeps and roughly how long one takes: a line each way. */
  plays: z
    .array(
      z.object({
        how: z.enum(["api", "page", "page_only"]).catch("api"),
        cases: z.array(z.string()).default([]),
        seconds: z.number().default(0),
        kept: z.string().nullable().default(null),
        text: z.string(),
      }),
    )
    .default([]),
  /** What a new session leaves behind here, in one sentence. */
  isolation_note: z.string().nullable().default(null),
  /** The code it will play, and whether the images here look like it; older servers don't say. */
  version: VersionSchema.nullish(),
  /** The version it plays: `disk`, or a ref evalution checks out and builds first. */
  ref: z.string().default("disk"),
  /** How its tries will be kept apart, the plan's status and rough timings; older servers don't say. */
  isolation: RunIsolationSchema.extend({
    status: z.enum(["none", "draft", "proven", "stale", "failed"]).catch("none").default("none"),
    stores: z.array(StoreWordsSchema).default([]),
    /** Seconds to start evalution's copy, last time. */
    start_seconds: z.number().nullable().default(null),
    /** Seconds to put the copy back before a try, last time. */
    restore_seconds: z.number().nullable().default(null),
    /** Seconds the plan's last rehearsal took. */
    rehearse_seconds: z.number().nullable().default(null),
  }).nullish(),
});
export type Estimate = z.infer<typeof EstimateSchema>;

/** Why a test failed in a run: the analysis of its first analysed failed try. */
export const FindingSchema = z.object({
  summary: ModelClaimSchema.nullish(),
  cause: ModelClaimSchema.nullish(),
  fix: z.string().nullish(),
  suspected: SuspectSchema.nullish(),
});
export type Finding = z.infer<typeof FindingSchema>;

export const RunCaseSchema = z.object({
  finding: FindingSchema.nullable().catch(null).default(null),
});

/** A recent conversation with the app a test can be drafted from. */
export const SourceSchema = z.object({
  session_id: z.string(),
  round_id: z.string().default(""),
  ask: z.string(),
  at: z.number(),
  tools: z.array(z.string()).default([]),
  reply: z.string().default(""),
});
export type Source = z.infer<typeof SourceSchema>;

export const AgentMessageSchema = z.object({
  id: z.string(),
  role: z.enum(["admin", "agent"]),
  at: z.number(),
  text: z.string(),
  read: z.array(z.string()).optional(),
  /** A test this message added to the set: the admin runs it from here. */
  saved: z.string().optional(),
  /** The plan this "Add to tests" message approved. */
  approves: z.string().optional(),
  plan: z
    .object({
      /** Absent on plans shown before plans had ids: those can't be approved from here. */
      id: z.string().optional(),
      steps: z.array(
        z.object({
          kind: z.enum(["message", "generated", "check"]),
          title: z.string(),
          note: z.string().optional(),
        }),
      ),
      tries: z.number(),
      case: z.string(),
      /** The time limit of a try, and where that number came from. */
      timeout: z.object({ seconds: z.number(), from: z.string() }).optional(),
      /** What this app can't serve of it, or what was asked of every test and it lacks. */
      flags: z.array(z.string()).optional(),
      /** Rubric lines a grader misread on replies written to meet and to miss them, each with
       * what to put in its place: a sharper line, or words for a code check. */
      weak_lines: z
        .array(
          z.object({
            line: z.string(),
            why: z.enum(["good_failed", "bad_passed", "unsure"]),
            use: z
              .union([
                z.object({ line: z.string() }),
                z.object({ contains: z.array(z.string()) }),
                z.object({ lacks: z.array(z.string()) }),
              ])
              .optional(),
          }),
        )
        .optional(),
      /** The test it would replace, and what that changes in what it checks. */
      replaces: z.string().optional(),
      changes: z.array(MeaningChangeSchema).optional(),
    })
    .optional(),
  /** A change the writer proposes to the isolation plan, approved like a test plan. */
  isolation: z
    .object({
      id: z.string(),
      version: z.number(),
      status: z.string(),
      from: z.string(),
      changes: z.array(z.string()).default([]),
    })
    .optional(),
  /** A page map the writer proposes: approving it lets it be tried with one real message. */
  page_map: z
    .object({
      id: z.string(),
      version: z.number(),
      status: z.string(),
      target: z.string().default(""),
      described: z.array(MapWordsSchema).default([]),
      evidence: z
        .array(
          z.object({
            entry: z.string(),
            file: z.string().nullable().default(null),
            line: z.number().nullable().default(null),
            tried: z.boolean().default(false),
          }),
        )
        .default([]),
    })
    .optional(),
  run: z.string().optional(),
  review: z
    .object({
      summary: z.string(),
      findings: z
        .array(
          z.object({
            suspected: SuspectSchema,
            text: z.string(),
            case: z.string().optional(),
            evidence: z.array(z.string()).optional(),
          }),
        )
        .default([]),
      proposal: z.string().optional(),
    })
    .optional(),
});
export type AgentMessage = z.infer<typeof AgentMessageSchema>;

/** One area of a learn as it goes: `listed` weighs nothing (no prompt, tool, state or trace hit)
 * and isn't learned unless asked; `kept` is current, learned before. `weight`: its skeleton
 * entries and trace hits; `steps`: the calls its agent may make, which grow with its weight. */
export const LearnAreaSchema = z.object({
  id: z.string(),
  files: z.number(),
  state: z.enum(["waiting", "running", "learned", "failed", "kept", "listed", "outside", "later"]),
  weight: z.number().optional(),
  steps: z.number().optional(),
  calls: z.number().default(0),
  tokens: z.number().default(0),
  now: z.string().default(""),
  since: z.number().optional(),
  seconds: z.number().optional(),
});
export type LearnArea = z.infer<typeof LearnAreaSchema>;

/** A learn's progress: the code map, the skeleton (its entries), the fallback's reading of files
 * no grammar reads (`read`), the areas, the overview. */
export const LearnProgressSchema = z.object({
  at: z.number(),
  stage: z.enum(["map", "skeleton", "read", "areas", "overview", "saving"]),
  files: z.number().optional(),
  skeleton: z.number().optional(),
  read: z.number().optional(),
  read_done: z.number().optional(),
  map_seconds: z.number().optional(),
  parallel: z.number().optional(),
  areas: z.array(LearnAreaSchema).default([]),
});
export type LearnProgress = z.infer<typeof LearnProgressSchema>;

/** An area as "Learn this area" found it: `current` was learned from what it is now (when:
 * `learned_at`), and nothing was spent unless asked again anyway. */
export const AreaStateSchema = z.object({
  id: z.string(),
  state: z.enum(["current", "moved", "due", "listed", "outside"]),
  weight: z.number().default(0),
  files: z.number().default(0),
  learned_at: z.number().nullable().default(null),
});
export type AreaState = z.infer<typeof AreaStateSchema>;

/** One stage of a learn (code map, read, modules, areas, overview, other): its seconds, and the
 * model calls, tokens and characters made while it ran. */
export const LearnStageSchema = z.object({
  stage: z.string(),
  seconds: z.number().default(0),
  calls: z.number().default(0),
  tokens_in: z.number().default(0),
  tokens_out: z.number().default(0),
  chars_in: z.number().default(0),
  chars_out: z.number().default(0),
});
export type LearnStage = z.infer<typeof LearnStageSchema>;

/** What one turn of the test writer took; a learn's, stage by stage. */
export const TurnUsageSchema = z.object({
  learn: z.boolean().default(false),
  at: z.number(),
  seconds: z.number().default(0),
  calls: z.number().default(0),
  stages: z.array(LearnStageSchema).default([]),
});
export type TurnUsage = z.infer<typeof TurnUsageSchema>;

export const AgentSessionSchema = z.object({
  id: z.string(),
  target: z.string().nullable().default(null),
  set: z.string(),
  status: z.enum(["idle", "working", "stopped"]),
  messages: z.array(AgentMessageSchema),
  /** What the agent did last, newest at the end: shown while it works. */
  activity: z.array(z.object({ at: z.number(), text: z.string() })).default([]),
  /** A learn's progress: its stage and each area's agent. */
  learn: LearnProgressSchema.nullable().default(null),
  /** What its turns took, newest last. */
  usage: z.array(TurnUsageSchema).default([]),
});
export type AgentSession = z.infer<typeof AgentSessionSchema>;

/** What "Learn this area" answered: the area as it stood, and the session when a learn started. */
export const AreaLearnSchema = z.object({
  area: AreaStateSchema,
  session: AgentSessionSchema.nullable().default(null),
});
export type AreaLearn = z.infer<typeof AreaLearnSchema>;

/** The app's earlier chats, latest first. */
export const AgentChatSchema = z.object({ id: z.string(), asked: z.string(), at: z.number() });

/** Where a line of the understanding comes from: `ref` names the file, section or traces. */
export const EvidenceSchema = z.object({
  kind: z.enum(["prompt", "code", "traces", "docs", "admin"]),
  ref: z.string().default(""),
});
export type Evidence = z.infer<typeof EvidenceSchema>;

const Steered = {
  verdict: z.enum(["right", "wrong"]).optional(),
  fix: z.string().optional(),
  /** The commit an update from the code last changed it from. */
  updated_from: z.string().optional(),
  /** A commit touched what it rests on, directly or through imports, and nothing read it yet. */
  stale: z.enum(["direct", "indirect"]).optional(),
  /** The owner steered it and the code has since moved: theirs to look at again. */
  flagged: z.boolean().optional(),
};

/** How what it knows keeps up with the code (docs/architecture.md "Keeping what it knows current"). */
export const KnowledgeSchema = z.object({
  mode: z.enum(["auto", "manual"]).default("manual"),
  out_of_date: z.number().default(0),
  flagged: z.number().default(0),
  can_update: z.boolean().default(false),
  /** The last update from a commit, until the owner has seen it. */
  last: z
    .object({ commit: z.string(), changed: z.number(), flagged: z.number() })
    .nullable()
    .default(null),
  changes: z
    .array(
      z.object({
        kind: z.enum(["added", "changed", "removed"]),
        before: z.string().optional(),
        after: z.string().optional(),
      }),
    )
    .default([]),
  can_undo: z.boolean().default(false),
});
export type Knowledge = z.infer<typeof KnowledgeSchema>;
const NO_KNOWLEDGE = KnowledgeSchema.parse({});

export const UpdatesSchema = z.object({ mode: z.enum(["auto", "manual"]) });

export const HooksSchema = z.object({
  available: z.boolean(),
  installed: z.boolean(),
  hooks: z.record(z.string(), z.string()).default({}),
  reason: z.string().optional(),
});
export type Hooks = z.infer<typeof HooksSchema>;

export const DiagramNodeSchema = z.object({
  id: z.string(),
  kind: z.enum(["person", "agent", "tools", "worker", "state"]),
  title: z.string(),
  sub: z.string().default(""),
  seen: z.boolean().default(true),
  evidence: z.array(EvidenceSchema).default([]),
  ...Steered,
});
export type DiagramNode = z.infer<typeof DiagramNodeSchema>;

export const ClaimSchema = z.object({
  id: z.string(),
  text: z.string(),
  evidence: z.array(EvidenceSchema).default([]),
  ...Steered,
});
export type Claim = z.infer<typeof ClaimSchema>;

/** An area of the app as What it learned lists it (ADR 0012): `current` was learned before from
 * what it is now, `learning` is being learned, `done` landed in this learn, `due` waits for one,
 * `listed` weighs nothing (no prompt, tool, state or trace hit) and is learned only when asked. */
export const LearnedAreaSchema = z.object({
  id: z.string(),
  title: z.string().default(""),
  state: z.enum(["current", "learning", "done", "due", "listed", "failed"]),
  weight: z.number().default(0),
  files: z.number().default(0),
  learned_at: z.number().nullable().default(null),
});
export type LearnedArea = z.infer<typeof LearnedAreaSchema>;

/** Where a learn stands, as the understanding carries it: its areas, heaviest first, and whether
 * the overview is a first pass (from the skeleton and the traces) or final. An older server sends
 * none; a newer one with no learn kept says `state: null` and no areas. */
export const LearnStateSchema = z.object({
  state: z.enum(["learning", "done", "stopped"]).nullable().default(null),
  started_at: z.number().nullable().default(null),
  areas: z.array(LearnedAreaSchema).default([]),
  overview: z.enum(["first_pass", "final"]).nullable().default(null),
});
export type LearnState = z.infer<typeof LearnStateSchema>;

/**
 * What the agent understood of a target (docs/architecture.md "The understanding"): the project
 * card, how it works, its usual paths, claims to check, what matters and the tests to start with.
 */
export const UnderstandingSchema = z.object({
  knowledge: KnowledgeSchema.default(NO_KNOWLEDGE),
  /** Where the latest learn stands; `null` from a server that doesn't say. */
  progress: LearnStateSchema.nullable().default(null),
  /** What the overview was written from: a first pass ("the code's skeleton and 12
   * conversations") until the areas are in, then final. */
  overview: z
    .object({
      state: z.enum(["first_pass", "final"]).nullable().default(null),
      basis: z.string().nullable().default(null),
    })
    .nullable()
    .default(null),
  version: z.number().default(0),
  written_at: z.number().default(0),
  fingerprint: z.string().default(""),
  card: z.object({
    name: z.string(),
    one_line: z.string().default(""),
    /** A few plain sentences on what the app is, for a person. */
    summary: z.string().default(""),
    stack: z.array(z.string()).default([]),
    status: z.enum(["learning", "up_to_date", "out_of_date"]).default("up_to_date"),
    changes: z.number().default(0),
    numbers: z.object({
      tools: z.number().default(0),
      tools_seen: z.number().default(0),
      state_kinds: z.array(z.string()).default([]),
      ask_seconds: z.number().nullable().default(null),
      tests: z.number().default(0),
      tests_passing: z.number().nullable().default(null),
      library_covered: z.number().default(0),
      library_total: z.number().default(0),
    }),
    watched: z.array(z.string()).default([]),
  }),
  diagram: z
    .object({
      nodes: z.array(DiagramNodeSchema).default([]),
      edges: z
        .array(
          z.object({
            from: z.string(),
            to: z.string(),
            asks: z.number().default(0),
            seen: z.boolean().default(true),
          }),
        )
        .default([]),
    })
    .default({ nodes: [], edges: [] }),
  /** `seen: false`: a path the code implies that real use never took. */
  paths: z
    .array(
      z.object({
        share: z.number(),
        steps: z.array(z.string()),
        seen: z.boolean(),
      }),
    )
    .default([]),
  sections: z
    .array(
      z.object({
        id: z.string(),
        title: z.string(),
        note: z.string().optional(),
        claims: z.array(ClaimSchema).default([]),
      }),
    )
    .default([]),
  priorities: z
    .array(
      z.object({
        id: z.string(),
        title: z.string(),
        picked: z.boolean().default(false),
        source: z.enum(["agent", "admin"]).default("agent"),
      }),
    )
    .default([]),
  starters: z
    .array(
      z.object({
        id: z.string(),
        title: z.string(),
        what: z.string().default(""),
        why: z.string().default(""),
        priority: z.string().optional(),
        library: z.string().optional(),
        selected: z.boolean().default(false),
        /** The set already has this test. */
        exists: z.boolean().default(false),
      }),
    )
    .default([]),
});
export type Understanding = z.infer<typeof UnderstandingSchema>;

/** One part of the app's skeleton, read off its code with no model (ADR 0012). */
export const SkeletonEntrySchema = z.object({
  id: z.string(),
  kind: z.enum(["entry", "prompt", "tool", "model", "route", "state", "agent"]),
  name: z.string(),
  path: z.string(),
  line: z.number(),
  /** How it was found or what it is: a store's kind, the call a model goes through. */
  what: z.string().default(""),
  /** The definition it sits in. */
  inside: z.string().default(""),
  /** A prompt's text, capped. */
  text: z.string().default(""),
  args: z.array(z.string()).default([]),
  model: z.string().default(""),
  /** The setting or name a model comes from, when the code doesn't write it out. */
  model_from: z.string().default(""),
  /** Its other places (`path:line`): copies, and the agents' settings that list a tool. */
  places: z.array(z.string()).default([]),
  /** For a tool: how many agents' settings list it. */
  agents: z.number().default(0),
  /** How often real use touched it in the window; null with no use to go by. */
  uses: z.number().nullable().default(null),
});
export type SkeletonEntry = z.infer<typeof SkeletonEntrySchema>;

/** The app's skeleton, and what real use named that the code doesn't. */
export const SkeletonSchema = z.object({
  fingerprint: z.string().default(""),
  entries: z.array(SkeletonEntrySchema).default([]),
  unseen: z
    .array(z.object({ kind: z.string(), name: z.string(), calls: z.number().default(0) }))
    .default([]),
  /** The use counted: conversations and asks read, from when to when (seconds). */
  window: z
    .object({
      sessions: z.number(),
      asks: z.number().default(0),
      since: z.number().nullable().default(null),
      until: z.number().nullable().default(null),
    })
    .nullable()
    .default(null),
});
export type AppSkeleton = z.infer<typeof SkeletonSchema>;

/** One steer: check a claim or a box, fix it, pick or add a priority, pick a starter. */
export type Steer =
  | { claim: string; verdict: "right" | "wrong" }
  | { node: string; verdict: "right" | "wrong" }
  | { claim: string; fix: string }
  | { node: string; fix: string }
  | { priority: string; picked: boolean }
  | { priority: { title: string } }
  | { starter: string; selected: boolean };

/** evalution's own model, as the settings page edits it (never the key). */
export const ModelSettingsSchema = z.object({
  provider: z.enum(["anthropic", "openai", "compatible"]),
  base_url: z.string(),
  strong: z.string(),
  light: z.string(),
  key_set: z.boolean(),
  /** `config`: set in evalution.yaml; `env`: the key comes from the environment. */
  source: z.enum(["page", "config", "env"]),
  /** Where each provider with a fixed address is. */
  addresses: z.record(z.string(), z.string()).default({}),
  /** From a save: a stored key stayed behind, since the address's host changed. */
  key_dropped: z.boolean().default(false),
});
export type ModelSettings = z.infer<typeof ModelSettingsSchema>;
export type Provider = ModelSettings["provider"];

/** A model the provider offers. */
export const ModelOptionSchema = z.object({
  id: z.string(),
  name: z.string(),
});
export type ModelOption = z.infer<typeof ModelOptionSchema>;

/** The provider's models, where they answered, and `note` when that isn't what was typed. */
export const ModelListSchema = z.object({
  data: z.array(ModelOptionSchema),
  base_url: z.string().optional(),
  note: z.string().optional(),
});

const ModelTestPartSchema = z.object({
  ok: z.boolean(),
  detail: z.string(),
  ms: z.number(),
});
export const ModelTestSchema = z.object({
  strong: ModelTestPartSchema,
  light: ModelTestPartSchema,
});
export type ModelTest = z.infer<typeof ModelTestSchema>;

export type ModelBody = {
  provider: Provider;
  base_url?: string;
  strong: string;
  light: string;
  key?: string;
};

/** How the app is doing over a period (GET /v1/targets/{target}/health), in plain words. */
export const HealthSchema = z.object({
  tiles: z
    .object({
      passing: z
        .object({
          value: z.number(),
          total: z.number(),
          delta: z.number().nullable().default(null),
          model_only: z.number().default(0),
          unjudged: z.number().default(0),
        })
        .nullable()
        .default(null),
      consistent: z.object({ value: z.number(), total: z.number() }).nullable().default(null),
      reply_seconds: z
        .object({
          value: z.number().nullable(),
          delta: z.number().nullable().default(null),
        })
        .nullable()
        .default(null),
    })
    .default({
      passing: null,
      consistent: null,
      reply_seconds: null,
    }),
  areas: z
    .array(
      z.object({
        name: z.string(),
        pct: z.number(),
        level: z.enum(["strong", "ok", "weak"]),
      }),
    )
    .default([]),
  trend: z
    .array(
      z.object({
        run: z.string(),
        at: z.number(),
        passing: z.number(),
        total: z.number(),
        marker: z.string().nullish(),
      }),
    )
    .default([]),
  attention: z
    .array(
      z.object({
        title: z.string(),
        detail: z.string().default(""),
        action: z.enum(["see_why", "open_test", "review"]).catch("review"),
        case: z.string().nullish(),
        run: z.string().nullish(),
      }),
    )
    .default([]),
  recent: z
    .array(
      z.object({
        run: z.string(),
        name: z.string(),
        at: z.number(),
        results: z.array(VerdictSchema).default([]),
      }),
    )
    .default([]),
  /** What changed in the code since it was last tested, and the tests that says to run. */
  code_change: z
    .object({
      summary: z.string().default(""),
      areas: z.array(z.string()).default([]),
      uncovered: z.array(z.string()).default([]),
      files: z.array(z.string()).default([]),
      file_count: z.number().default(0),
      /** The model read this change; else it is only the changed files and all the tests. */
      read: z.boolean().default(true),
      tests: z.array(z.object({ set: z.string(), case: z.string(), name: z.string() })).default([]),
    })
    .nullable()
    .default(null),
  knowledge: KnowledgeSchema.default(NO_KNOWLEDGE),
  /** The latest run: what it came to, from its checks, and the model's reading of it. */
  report: z
    .object({
      run: z.string(),
      facts: z.object({
        passed: z.number(),
        total: z.number(),
        model_only: z.number().default(0),
        held: z
          .array(
            z.object({
              case: z.string(),
              name: z.string(),
              passed: z.number(),
              judged: z.number(),
              basis: z.string(),
              note: z.string().nullable().default(null),
            }),
          )
          .default([]),
        failed: z
          .array(
            z.object({
              case: z.string(),
              name: z.string(),
              verdict: VerdictSchema,
              label: z.string().nullable().default(null),
              reason: z.string().nullable().default(null),
            }),
          )
          .default([]),
        unjudged: z
          .array(
            z.object({
              case: z.string(),
              name: z.string(),
              label: z.string().nullable().default(null),
              reasons: z.array(z.string()).default([]),
            }),
          )
          .default([]),
        regressions: z.array(z.object({ case: z.string(), title: z.string() })).default([]),
      }),
      well: z
        .array(
          ModelClaimSchema.extend({
            case: z.string().nullable().default(null),
          }),
        )
        .default([]),
      reading: z
        .array(
          ModelClaimSchema.extend({
            case: z.string().nullable().default(null),
          }),
        )
        .default([]),
      next: z
        .array(
          ModelClaimSchema.extend({
            case: z.string().nullable().default(null),
          }),
        )
        .default([]),
    })
    .nullable()
    .default(null),
  report_pending: z.boolean().default(false),
  /** A look for code changes is going; the card may follow. */
  checking: z.boolean().default(false),
  /** How tests run: the isolation plan in plain words, whatever its status (older servers send it
   * only when a commit left it out of date). */
  isolation: z
    .object({
      status: z.enum(["none", "draft", "proven", "stale", "failed"]).catch("none"),
      version: z.number().nullable().default(null),
      /** When the plan was last saved: proven, for a proven one. */
      updated: z.number().nullable().default(null),
      stores: z.array(StoreWordsSchema).default([]),
      /** Left alone, kept as a record of what happened. */
      exclude: z.array(z.string()).default([]),
      /** Where the last rehearsal found what the plan misses. */
      leaks: z.array(z.string()).default([]),
      stale_files: z.array(z.string()).default([]),
      /** How tries are kept apart without a plan. */
      plain: IsolationHowSchema.default("none"),
      /** What the connection itself promises, in one sentence. */
      note: z.string().nullable().default(null),
      /** The app's code folder is known, so isolation can be found. */
      can_run: z.boolean().default(true),
      problem: z.string().nullable().default(null),
    })
    .nullable()
    .default(null),
  /** The page map, only when a commit left it out of date or the check before a run found it
   * broken. */
  page: z
    .object({
      status: z.enum(["stale", "broken"]).catch("stale"),
      stale_files: z.array(z.string()).default([]),
      missing: z.array(z.string()).default([]),
      /** The page as the check found it, lacking what's missing: a screenshot URL. */
      shot: z.string().nullable().default(null),
    })
    .nullable()
    .catch(null)
    .default(null),
});
export type Health = z.infer<typeof HealthSchema>;
export type Period = "7d" | "30d" | "all";

/** A time from the account routes: epoch seconds, or an ISO string; read as seconds. */
const AtSchema = z
  .union([z.number(), z.string()])
  .transform((at) => (typeof at === "number" ? at : Date.parse(at) / 1000));

export const WorkspaceSchema = z.object({
  id: z.string(),
  name: z.string(),
  personal: z.boolean().default(false),
});
export type WorkspaceInfo = z.infer<typeof WorkspaceSchema>;

/** A person's own model key: only its last four characters ever come back. */
export const ModelKeySchema = z.object({
  set: z.boolean(),
  last4: z.string().nullable().default(null),
});

/** Who is asking, in which workspace (GET /v1/me). Local: not hosted, no user, the one workspace. */
export const MeSchema = z.object({
  hosted: z.boolean().default(false),
  user: z.object({ id: z.string(), email: z.string(), name: z.string().default("") }).nullable(),
  workspace: WorkspaceSchema.nullable(),
  workspaces: z.array(WorkspaceSchema).default([]),
  model_key: ModelKeySchema.default({ set: false, last4: null }),
});
export type Me = z.infer<typeof MeSchema>;

export const MemberSchema = z.object({
  id: z.string(),
  email: z.string(),
  name: z.string().default(""),
  joined_at: AtSchema,
});
export type Member = z.infer<typeof MemberSchema>;

export const InviteSchema = z.object({ url: z.string(), expires_at: AtSchema.optional() });

export const AcceptedSchema = z.object({ workspace: WorkspaceSchema });

export const TokenScopeSchema = z.enum(["all", "traces"]);
export type TokenScope = z.infer<typeof TokenScopeSchema>;

export const ApiTokenSchema = z.object({
  id: z.string(),
  name: z.string(),
  prefix: z.string(),
  created_at: AtSchema,
  last_used_at: AtSchema.nullable().default(null),
  /** `traces`: it can only send spans. */
  scope: TokenScopeSchema.default("all"),
});
export type ApiToken = z.infer<typeof ApiTokenSchema>;

/** A token just made: the only time its value is sent. */
export const NewTokenSchema = z.object({
  id: z.string(),
  name: z.string(),
  prefix: z.string(),
  token: z.string(),
});

// ---- Connect, setup, test environment, what it learned (next level, U2) ----

/** A setting that would reach a service outside the test copy, by name only (B7): it starts
 * empty on the copy unless `allowed`. */
export const OutboundSchema = z.object({
  name: z.string(),
  kind: z.string().default("credential"),
  allowed: z.boolean().default(false),
});
export type Outbound = z.infer<typeof OutboundSchema>;

/** How a run puts the copy back between tries (B1): `hot` is what the rehearsal proved of doing
 * it with the app up; `restart`, every put back stops and starts the whole copy. */
export const PutBackSchema = z.object({
  hot: z.enum(["proven", "failed"]).nullable().catch(null).default(null),
  app_holds_state: z.boolean().default(false),
  restart: z.boolean().default(true),
});
export type PutBack = z.infer<typeof PutBackSchema>;

/** The isolation view with what leaves the copy and how it is put back. */
export const IsolationViewSchema = IsolationSchema.extend({
  outbound: z.array(OutboundSchema).catch([]).default([]),
  put_back: PutBackSchema.nullable().catch(null).default(null),
});
export type IsolationView = z.infer<typeof IsolationViewSchema>;

export const OutboundListSchema = z.object({ outbound: z.array(OutboundSchema).default([]) });

/** The repository a discovery cloned, when the field named one (`owner/repo#412`). */
export const RepositorySchema = z.object({
  host: z.string().default("github.com"),
  owner: z.string(),
  repo: z.string(),
  pull: z.number().nullable().default(null),
  ref: z.string().nullable().default(null),
  said: z.string().default(""),
});
export type Repository = z.infer<typeof RepositorySchema>;

export const DiscoveryViewSchema = DiscoverySchema.extend({
  repository: RepositorySchema.nullable().catch(null).default(null),
});
export type DiscoveryView = z.infer<typeof DiscoveryViewSchema>;

/** An area as What it learned lists it, with `outside` (not part of the agent, learned only
 * when asked) and, for a failed one, why. */
export const AreaViewSchema = LearnedAreaSchema.extend({
  state: z.enum(["current", "learning", "done", "due", "listed", "failed", "outside"]).catch("due"),
  reason: z.string().nullable().catch(null).default(null),
});
export type AreaView = z.infer<typeof AreaViewSchema>;

/** Where the latest learn stands, with what the code's watch left out. */
export const LearnStateViewSchema = LearnStateSchema.extend({
  areas: z.array(AreaViewSchema).default([]),
  left_out: z
    .object({
      past_cap: z.number().default(0),
      too_large: z.number().default(0),
    })
    .catch({ past_cap: 0, too_large: 0 })
    .prefault({}),
});
export type LearnStateView = z.infer<typeof LearnStateViewSchema>;

/** What the rendered profile holds: `areas` of `of`, and the titles it left out to fit. */
export const ProfileFitSchema = z.object({
  areas: z.number().default(0),
  of: z.number().default(0),
  left_out: z.array(z.string()).default([]),
});
export type ProfileFit = z.infer<typeof ProfileFitSchema>;

export const UnderstandingViewSchema = UnderstandingSchema.extend({
  progress: LearnStateViewSchema.nullable().catch(null).default(null),
  profile: ProfileFitSchema.nullable().catch(null).default(null),
});
export type UnderstandingView = z.infer<typeof UnderstandingViewSchema>;

// ---- U3: the result screens (appended; see docs/judging.md "Timeline" and docs/versions.md) ----

/** Text the server may send as a string or as something else: kept as words either way. */
const WordsSchema = z
  .string()
  .nullable()
  .catch((ctx) => (ctx.input === undefined ? null : JSON.stringify(ctx.input)))
  .default(null);

/** One line a reply was held to: a rubric line, the reference, `reply.contains` / `reply.lacks`. */
export const TimelineLineSchema = z.object({
  id: z.string(),
  text: z.string().default(""),
  status: TryOutcomeSchema.nullable().catch(null).default(null),
  /** `code` or `model`. */
  by: z.string().nullable().default(null),
  /** `must` or `guide`. */
  role: z.string().nullable().default(null),
  reason: z.string().nullable().default(null),
});
export type TimelineLine = z.infer<typeof TimelineLineSchema>;

/**
 * One entry of a try's "Step by step" (docs/judging.md, "Timeline"): a message, an expected call
 * against the one made, a reply with its graded lines, a state check, a page frame, a break, how
 * the copy was put back before, or the page's health. In time order.
 */
export const TimelineEntrySchema = z.object({
  kind: z.string(),
  status: TryOutcomeSchema.nullable().catch(null).default(null),
  span: z.string().nullable().catch(null).default(null),
  at: z.number().nullable().catch(null).default(null),
  /** `person`, `main`, a delegated agent's name or `helper`, `page`, `code`. */
  actor: z.string().nullable().catch(null).default(null),
  turn: z.number().nullable().catch(null).default(null),
  text: z.string().nullable().catch(null).default(null),
  filler: z.boolean().catch(false).default(false),
  /** A call's step: `matched`, `missing`, `wrong_args`, `late`, `never`, `extra`, `unjudged`,
   * `automatic`. */
  step: z.string().nullable().catch(null).default(null),
  expected: WordsSchema,
  made: WordsSchema,
  reason: z.string().nullable().catch(null).default(null),
  automatic: z.boolean().catch(false).default(false),
  error: z.string().nullable().catch(null).default(null),
  lines: z.array(TimelineLineSchema).catch([]).default([]),
  shown_at: z.number().nullable().catch(null).default(null),
  instead: z.string().nullable().catch(null).default(null),
  shot: z.string().nullable().catch(null).default(null),
  key: z.string().nullable().catch(null).default(null),
  how: z.string().nullable().catch(null).default(null),
  seconds: z.number().nullable().catch(null).default(null),
  checked: z.array(z.string()).catch([]).default([]),
  problem: z.string().nullable().catch(null).default(null),
  why: z.string().nullable().catch(null).default(null),
});
export type TimelineEntry = z.infer<typeof TimelineEntrySchema>;

/** A try's result with its one list, when the server sends it (`steps_timeline`); older servers
 * don't, and the page builds the same list from the plan steps, `tool_rows` and the trajectory. */
export const TryResultSchema = CompareSchema.extend({
  steps_timeline: z.array(TimelineEntrySchema).nullable().catch(null).default(null),
});
export type TryResult = z.infer<typeof TryResultSchema>;

/** A pull request fetched into evalution's own refs (`POST .../versions/fetch {pull}`). */
export const FetchedPullSchema = z.object({
  remote: z.string().default(""),
  pull: z.number(),
  /** The ref a run plays it by: `pull/412`. */
  ref: z.string(),
  commit: z.string(),
});
export type FetchedPull = z.infer<typeof FetchedPullSchema>;

/** A ref of the app's repository, with the kinds newer servers send (`pull`). */
export const VersionRefNextSchema = VersionRefSchema.extend({
  kind: z.enum(["local", "remote", "fetched", "tag", "pull"]).catch("local"),
});
export type VersionRefNext = z.infer<typeof VersionRefNextSchema>;

/** The app's repository, its pull requests listed among the refs. */
export const VersionsNextSchema = VersionsSchema.extend({
  refs: z.array(VersionRefNextSchema).default([]),
});
export type VersionsNext = z.infer<typeof VersionsNextSchema>;
