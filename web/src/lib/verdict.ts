import type { Actual, Layers, RunTest, StepPage, TryOutcome, Verdict } from "./schema";

type Layer = Layers["stored"];

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

/** A pass on a target reached through its page alone: the reply is what the page showed, and
 * nothing the app stored or sent the model was there to check. */
export const PAGE_ONLY = "reply only, through its page";
/** A run of such a target, in a sentence. */
export const PAGE_ONLY_RUN =
  "Played through its page only: each reply is what the page showed, so a pass is reply only.";
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
  grader_unreliable: "grader unreliable here",
  page_map: "the page lacks what its page map names",
  no_reply: "the turn had no reply to show",
};

/** A line whose grader a person disagrees with too often: its passes don't count. */
export const UNRELIABLE = WHY_UNJUDGED.grader_unreliable;

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

// A try played through the app's page (ADR 0010): which layer held, and what the page showed.

const LAYER_NAME: Record<keyof Layers, string> = {
  stored: "Stored",
  answered: "Answered",
  shown: "Shown",
};

const LAYER_GLYPH: Record<Layer, string> = { pass: "✓", fail: "✕", unjudged: "?", absent: "" };
const LAYER_SAID: Record<Layer, string> = {
  pass: "held",
  fail: "failed",
  unjudged: "couldn't judge",
  absent: "",
};

/** One layer of a try, as a pill: `Stored ✓`. */
export type LayerPill = { layer: keyof Layers; text: string; label: string; state: Layer };

/** What the banner says of a try's layers. */
export type LayerWords = {
  pills: LayerPill[];
  /** Said in place of the try's outcome: only the page failed it, or the app didn't take the
   * page's message. */
  headline: string | null;
  /** Only the page failed: through the API alone, the try would have passed. */
  apiAlone: boolean;
  /** What the banner adds in a sentence: {@link API_ALONE}, or that the page only guided. */
  line: string | null;
};

export const API_ALONE = "Through the API alone, this try would have passed.";
/** A try that passed although the page failed: the test doesn't let the page decide. */
export const PAGE_GUIDES =
  "The page didn't show everything, but this test doesn't let the page decide.";

/**
 * What the banner says of a try whose message the app refused (its status) or never took (no
 * status): the app's, never "failed on the page".
 */
export function notTakenWords(notTaken: { status: number | null }): string {
  return notTaken.status === null
    ? "The app didn't take the message"
    : `The app refused the message (${notTaken.status})`;
}

/**
 * The banner's layers: which held, and the plain words for a try only the page failed; `null` for
 * a try played through the API alone. `withoutPage` is how the try comes out on every check but
 * the page's, when the server says. `pageOnly`: the target is reached through its page alone, so
 * there is no API it would have passed through. `notTaken`: the app refused the page's message,
 * or never took it, which is the app's, whatever the layers say.
 */
export function layerWords(
  layers: Layers | null | undefined,
  outcome: TryOutcome,
  withoutPage?: TryOutcome | null,
  pageOnly = false,
  notTaken?: { status: number | null } | null,
): LayerWords | null {
  if (!layers || layers.shown === "absent") return null;
  const order = ["stored", "answered", "shown"] as const;
  const pills = order
    .filter((layer) => layers[layer] !== "absent")
    .map((layer) => ({
      layer,
      text: `${LAYER_NAME[layer]} ${LAYER_GLYPH[layers[layer]]}`,
      label: `${LAYER_NAME[layer]}: ${LAYER_SAID[layers[layer]]}`,
      state: layers[layer],
    }));
  const held = (layer: Layer) => layer === "pass" || layer === "absent";
  const onlyShown = layers.shown === "fail" && held(layers.stored) && held(layers.answered);
  const apiAlone = !pageOnly && onlyShown && (withoutPage ?? "passed") === "passed";
  let headline: string | null = null;
  if (notTaken && outcome !== "passed") {
    headline = notTakenWords(notTaken);
  } else if (onlyShown && outcome === "failed") {
    headline =
      layers.stored === "pass"
        ? "Failed on the page, not in memory"
        : layers.answered === "pass"
          ? "Failed on the page, not in the answer"
          : "Failed on the page";
  }
  const line =
    outcome === "passed"
      ? layers.shown === "fail"
        ? PAGE_GUIDES
        : null
      : apiAlone
        ? API_ALONE
        : null;
  return { pills, headline, apiAlone, line };
}

/** What the page showed of one message, in a line, and whether that held. */
export function shownWords(page: StepPage): { held: boolean | null; text: string } {
  const outcome = page.check?.outcome;
  if (outcome === "unjudged") return { held: null, text: "Nothing to show: the turn had no reply" };
  if (page.shown !== null && outcome !== "failed") {
    return { held: outcome === "passed" ? true : null, text: "Shown on the page" };
  }
  const instead = (page.shown ?? page.instead).trim();
  return {
    held: false,
    text: instead ? `Not shown — the page showed: “${instead}”` : "Not shown on the page",
  };
}

/** Who decides a page check: code, from the page. Never a model. */
export const CHECKED_ON_PAGE = "checked on the page";

const KIND_WORD: Record<string, string> = {
  shown: "on the page",
  page_health: "the page's health",
};

/** A check kind's few words, for the kinds a reader should tell apart; `null` for the rest. */
export const checkKindWord = (kind: string | null | undefined) =>
  kind ? (KIND_WORD[kind] ?? null) : null;
