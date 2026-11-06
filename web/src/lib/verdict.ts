import type { Actual, RunTest, TryOutcome, Verdict } from "./schema";

// The words for how a test, a try or a check came out, so every screen says the same.

const WORD: Record<NonNullable<RunTest["verdict"]>, string> = {
  pass: "Passed",
  flaky: "Flaky",
  fail: "Failed",
  unjudged: "Couldn't judge",
};

export const verdictWord = (verdict: keyof typeof WORD) => WORD[verdict];

const TRY_WORD: Record<TryOutcome, string> = {
  passed: WORD.pass,
  failed: WORD.fail,
  unjudged: WORD.unjudged,
};

/** How a single try came out. */
export const tryWord = (outcome: TryOutcome) => TRY_WORD[outcome];

/** A check on a step: held, failed, or nobody could say. */
export const checkWord = (actual: Pick<Actual, "passed" | "unjudged">) =>
  actual.unjudged ? WORD.unjudged : actual.passed ? "Held" : WORD.fail;

/** A test that passed, but not by the expected steps. */
export const PASSED_DIFFERENT_ROUTE = `${WORD.pass}, different route`;

/** A test that passed on what a model read alone: nothing in the code showed the outcome. */
export const PASSED_MODEL_ONLY = `${WORD.pass}, judged by a model only`;

/** A model's words, not a result. */
export const MODEL_ONLY = "judged by a model only";
export const ITS_READING = "Analysis";

/** A model's claim whose refs or quotes code couldn't find in the evidence. */
export const NOT_VERIFIED = "Not verified";
export const SUGGESTION = "Suggestion";

/** A run in a line: what it came to. */
export function headline(facts: { passed: number; total: number; model_only: number }): string {
  const judged = `${facts.passed} of ${facts.total} tests passed`;
  return facts.model_only ? `${judged}, ${facts.model_only} judged by a model only` : judged;
}

const WHY_UNJUDGED: Record<string, string> = {
  no_trace_source: "the app has no traces",
  no_traces: "traces didn't arrive",
  partial_trace: "only part of the traces arrived",
  content_not_captured: "the traces don't hold what the model was sent",
  judge_failed: "the grader failed",
  judge_unsure: "the grader wasn't sure",
  app_unreachable: "the app couldn't be reached",
  not_supported: "the app can't answer this check",
  adapter_misread: "the adapter didn't read the app",
  blocked: "the app stopped to wait on something",
  never_started: "no sign of the agent working",
  credentials_refused: "the app refused the credentials",
  app_busy: "the app or its gateway was busy or down",
  leftover_state: "it started with leftover state",
};

/** Why a test couldn't be judged, in plain words. */
export const unjudgedWords = (reasons: readonly string[]) =>
  reasons.map((reason) => WHY_UNJUDGED[reason] ?? reason).join(", ");

const ORIGINAL: Record<Verdict, string> = {
  pass: "passed",
  flaky: "flaky",
  fail: "failed",
  unjudged: "not judged",
};

/** A result today's rules read differently from when it was played: no change in the app. */
export const originallyWord = (was: Verdict) => `Originally ${ORIGINAL[was]}; rules changed`;

const RETRIED: Record<"app" | "grader", string> = {
  app: "The app wasn't available at first, so this was tried again.",
  grader: "The grader failed at first, so it was asked again.",
};

/** What had to be tried a second time, in plain words. */
export const retriedWords = (retried: readonly { what: "app" | "grader" }[]) =>
  retried.map((entry) => RETRIED[entry.what]);

/** A try that never got as far as the assistant. */
export const NEVER_REACHED = "The assistant was never reached.";

/** How a test came out, in a line. */
export function verdictLine(
  test: Pick<RunTest, "verdict" | "label" | "basis" | "different">,
): string {
  if (!test.verdict) return "";
  if (test.verdict === "flaky") return WORD.flaky;
  if (test.verdict !== "pass") return test.label ?? WORD[test.verdict];
  if (test.basis === "model_only") return PASSED_MODEL_ONLY;
  return test.different ? PASSED_DIFFERENT_ROUTE : WORD.pass;
}
