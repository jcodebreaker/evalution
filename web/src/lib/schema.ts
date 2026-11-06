import * as z from "zod";

// The page's view of the server's JSON (docs/architecture.md
// "API and page"). Times are seconds, money is USD. Parsing is lenient: fields a
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
    .object({ path: z.string(), ignore: z.array(z.string()).default([]) })
    .nullable()
    .default(null),
  secrets: z.array(z.string()).default([]),
  /** From a save: stored secrets that stayed behind, since a host changed. */
  secrets_dropped: z.array(z.string()).default([]),
  managed: z.boolean().default(false),
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
          usd: z.number().nullable().default(null),
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
          usd: z.number().nullable().default(null),
          messages: z.number().nullable().default(null),
          trace_url: z.string().nullable().default(null),
          models: z.array(z.string()).default([]),
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
  /** How to send the app's traces to evalution's own Phoenix, when it runs one. */
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
  /** The app's own Phoenix; `null` when evalution's own Phoenix is all it has. */
  phoenix: { url: string; project: string } | null;
  code: { path: string; ignore: string[] } | null;
  secrets: Record<string, string>;
};

/** Whether evalution runs its own Phoenix, and where. */
export const TracesSchema = z.object({
  managed: z.boolean(),
  url: z.string().nullable(),
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
  usd: z.number(),
  tokens: z.number(),
});
export type Totals = z.infer<typeof TotalsSchema>;

export const PassMarksSchema = z.object({
  tools: z.number(),
  reply: z.number(),
});
export type PassMarks = z.infer<typeof PassMarksSchema>;

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
  usd: z.number(),
});
export type RunTest = z.infer<typeof RunTestSchema>;

/** Where a run played: by a hosted server, or by `evalution run` and uploaded; and its name. */
export const EnvironmentSchema = z.object({
  where: z.enum(["hosted", "local"]),
  name: z.string().nullable().default(null),
});
export type Environment = z.infer<typeof EnvironmentSchema>;

export const RunSummarySchema = z.object({
  run: z.string(),
  target: z.string().nullable().default(null),
  set: z.string(),
  started_at: z.number(),
  environment: EnvironmentSchema.default({ where: "local", name: null }),
  status: z.enum(["running", "done", "stopped", "failed"]),
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
  usd: z.number().nullable().default(null),
  /** The run the latest result comes from, and when it started. */
  last_run: z.string().nullable().default(null),
  last_at: z.number().nullable().default(null),
  /** Why the test may be out of date with its target, from the target's drift. */
  stale: z.string().nullable().default(null),
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
      usd_pct: z.number(),
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
  usd: z.number().optional(),
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
});
export type LaneStep = z.infer<typeof LaneStepSchema>;

const CheckRowSchema = z.object({
  id: z.string(),
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

/** The plan with one try of a run laid over it. */
const ComparedSideSchema = z
  .object({ verdict: z.string(), passed: z.number(), judged: z.number() })
  .nullable();

/** One run against another (GET /v1/evals/runs/{run}/comparison). */
export const ComparisonSchema = z.object({
  run: z.string(),
  base: z.string().nullable().default(null),
  answer: z.enum(["worse", "mixed", "better", "same"]).catch("same"),
  headline: z.string().default(""),
  could_be_chance: z.boolean().default(false),
  changed: z.array(z.object({ what: z.string(), before: z.string(), now: z.string() })).default([]),
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

export const CompareSchema = TestPlanSchema.extend({
  run: z.string(),
  verdict: VerdictSchema.nullable().default(null),
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
  usd: z.number().nullable().default(null),
  /** What's missing of the try's traces, when any is. */
  trace_note: z.string().nullable().default(null),
  explain: ExplainSchema.optional(),
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
  usd: z.number(),
  first_run: z.boolean().default(false),
  /** Tests this app can't serve, left out of the run and the figure. */
  refused: z.array(z.object({ case: z.string(), why: z.string() })).default([]),
  /** What a new session leaves behind here, in one sentence. */
  isolation_note: z.string().nullable().default(null),
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
      usd: z.number(),
      case: z.string(),
      /** The time limit of a try, and where that number came from. */
      timeout: z.object({ seconds: z.number(), from: z.string() }).optional(),
      /** What this app can't serve of it, or what was asked of every test and it lacks. */
      flags: z.array(z.string()).optional(),
      /** The test it would replace, and what that changes in what it checks. */
      replaces: z.string().optional(),
      changes: z.array(MeaningChangeSchema).optional(),
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

export const AgentSessionSchema = z.object({
  id: z.string(),
  target: z.string().nullable().default(null),
  set: z.string(),
  status: z.enum(["idle", "working", "stopped"]),
  spent_usd: z.number(),
  messages: z.array(AgentMessageSchema),
  /** What the agent did last, newest at the end: shown while it works. */
  activity: z.array(z.object({ at: z.number(), text: z.string() })).default([]),
});
export type AgentSession = z.infer<typeof AgentSessionSchema>;

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
  update_usd: z.number().nullable().default(null),
  read_usd: z.number().nullable().default(null),
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

/**
 * What the agent understood of a target (docs/architecture.md "The understanding"): the project
 * card, how it works, its usual paths, claims to check, what matters and the tests to start with.
 */
export const UnderstandingSchema = z.object({
  knowledge: KnowledgeSchema.default(NO_KNOWLEDGE),
  version: z.number().default(0),
  written_at: z.number().default(0),
  fingerprint: z.string().default(""),
  card: z.object({
    name: z.string(),
    one_line: z.string().default(""),
    stack: z.array(z.string()).default([]),
    status: z.enum(["learning", "up_to_date", "out_of_date"]).default("up_to_date"),
    changes: z.number().default(0),
    numbers: z.object({
      tools: z.number().default(0),
      tools_seen: z.number().default(0),
      state_kinds: z.array(z.string()).default([]),
      ask_seconds: z.number().nullable().default(null),
      ask_usd: z.number().nullable().default(null),
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
        usd_per_try: z.number().default(0),
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
  spent_usd: z.number().default(0),
  /** Where each provider with a fixed address is. */
  addresses: z.record(z.string(), z.string()).default({}),
  /** From a save: a stored key stayed behind, since the address's host changed. */
  key_dropped: z.boolean().default(false),
});
export type ModelSettings = z.infer<typeof ModelSettingsSchema>;
export type Provider = ModelSettings["provider"];

/** A model the provider offers, with its USD per million tokens when known. */
export const ModelOptionSchema = z.object({
  id: z.string(),
  name: z.string(),
  input: z.number().optional(),
  output: z.number().optional(),
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
      cost: z
        .object({
          per_question: z.number().nullable(),
          period_total: z.number(),
        })
        .nullable()
        .default(null),
    })
    .default({
      passing: null,
      consistent: null,
      reply_seconds: null,
      cost: null,
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
        usd: z.number().default(0),
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

export const ApiTokenSchema = z.object({
  id: z.string(),
  name: z.string(),
  prefix: z.string(),
  created_at: AtSchema,
  last_used_at: AtSchema.nullable().default(null),
});
export type ApiToken = z.infer<typeof ApiTokenSchema>;

/** A token just made: the only time its value is sent. */
export const NewTokenSchema = z.object({
  id: z.string(),
  name: z.string(),
  prefix: z.string(),
  token: z.string(),
});
