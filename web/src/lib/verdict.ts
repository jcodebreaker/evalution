import type { Actual, Compare, Layers, RunTest, StepPage, TryOutcome, Verdict } from "./schema";

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

/** A layer of a try, by name: `Stored`, `Page healthy`. */
export const LAYER_NAME: Record<keyof Layers, string> = {
  stored: "Stored",
  answered: "Answered",
  shown: "Shown",
  page_health: "Page healthy",
};

const LAYER_GLYPH: Record<Layer, string> = { pass: "✓", fail: "✕", unjudged: "?", absent: "" };
/** How a layer came out, in a word or two: `held`, `failed`. */
export const LAYER_SAID: Record<Layer, string> = {
  pass: "held",
  fail: "failed",
  unjudged: "couldn't judge",
  absent: "",
};

/** One layer of a try, as a pill: `Stored ✓`; `healthy`: the page's health, said apart. */
export type LayerPill = {
  layer: keyof Layers | "healthy";
  text: string;
  label: string;
  state: Layer;
};

const OF_TRY: Record<TryOutcome, Layer> = { passed: "pass", failed: "fail", unjudged: "unjudged" };

/** A test's row in a line: which layer held, `Stored ✓ · Answered ✓ · Shown ✕`; `null` for a try
 * played through the API alone, or a server that doesn't say. */
export function layerLine(layers: Layers | null | undefined): string | null {
  if (!layers || layers.shown === "absent") return null;
  return (["stored", "answered", "shown", "page_health"] as const)
    .filter((layer) =>
      // The page's health is said only when it failed, as the try's banner shows it apart.
      layer === "page_health" ? layers[layer] === "fail" : layers[layer] !== "absent",
    )
    .map((layer) => `${LAYER_NAME[layer]} ${LAYER_GLYPH[layers[layer] ?? "absent"]}`)
    .join(" · ");
}

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
 * The page's message the app refused (its `status`) or never took (no status); `silent`: it never
 * answered it at all, said by a try the server stored before it kept which.
 */
export type NotTaken = { status: number | null; silent?: boolean };

/**
 * What the banner says of a try whose message the app refused (its status) or never took (no
 * status): the app's, never "failed on the page".
 */
export function notTakenWords(notTaken: NotTaken): string {
  if (notTaken.silent) return "The app never answered the message";
  return notTaken.status === null
    ? "The app didn't take the message"
    : `The app refused the message (${notTaken.status})`;
}

/**
 * Whether the app refused or never took the page's message on the try shown: the server's word
 * when it says, else read from the try: played through the page, it didn't pass, never reached
 * the assistant, came back with no reply, and its answer couldn't be judged or failed (a try
 * stored before the server kept which, or one that ran out of time waiting on the message).
 * `null`: the app took it, or the try isn't the page's.
 */
export function notTakenOf(
  shown: Pick<Compare, "page" | "called" | "layers" | "output" | "outcome">,
): NotTaken | null {
  const page = shown.page;
  if (!page || shown.outcome === "passed") return null;
  if (page.not_taken) return page.not_taken;
  const answered = shown.layers?.answered;
  const silent =
    !shown.called &&
    !page.broken_map &&
    !shown.output?.reply &&
    (answered === "fail" || answered === "unjudged");
  return silent ? { status: null, silent: true } : null;
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
  notTaken?: NotTaken | null,
  health?: { healthy: TryOutcome | null; shownAlone: Layer | null } | null,
): LayerWords | null {
  if (!layers || layers.shown === "absent") return null;
  const order = ["stored", "answered", "shown"] as const;
  // The page's health said apart when the server does: Shown is then what showed of the replies.
  const apart = health?.healthy ? health : null;
  const state = (layer: (typeof order)[number]): Layer =>
    layer === "shown" && apart ? (apart.shownAlone ?? layers.shown) : layers[layer];
  const pills: LayerPill[] = order
    .filter((layer) => state(layer) !== "absent")
    .map((layer) => ({
      layer,
      text: `${LAYER_NAME[layer]} ${LAYER_GLYPH[state(layer)]}`,
      label: `${LAYER_NAME[layer]}: ${LAYER_SAID[state(layer)]}`,
      state: state(layer),
    }));
  if (apart?.healthy) {
    const healthy = OF_TRY[apart.healthy];
    pills.push({
      layer: "healthy",
      text: `Page healthy ${LAYER_GLYPH[healthy]}`,
      label: `Page healthy: ${LAYER_SAID[healthy]}`,
      state: healthy,
    });
  }
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

/** What a failed page try asks of the person: one sentence and the one button that follows it. */
export type NextStep = { text: string; button: string };

/**
 * The next step after a page try that didn't pass, by whose it is (ADR 0010); `null` where the
 * try says nothing that one step would fix (a broken page map has its own note). The app refused
 * or never took the message: the app's, run again once it answers. Only the page failed and the
 * reply held: the page's to fix, then run again; when every reply showed, the page's health is
 * what failed, said as the server's reason says it.
 */
export function pageNextStep(
  shown: Pick<
    Compare,
    "page" | "called" | "layers" | "output" | "outcome" | "retried" | "reason" | "steps"
  >,
  app: string,
): NextStep | null {
  const page = shown.page;
  if (!page || shown.outcome === "passed" || page.broken_map) return null;
  const notTaken = notTakenOf(shown);
  if (notTaken) {
    const what = notTaken.silent
      ? `${app} never answered the page's message`
      : notTaken.status === null
        ? `${app} didn't take the page's message`
        : `${app} refused the page's message (${notTaken.status})`;
    const again = shown.retried.some((entry) => entry.what === "app")
      ? " It was already tried a second time."
      : "";
    return {
      text: `${what}, so this is ${app}'s, not the page's.${again} Run it again once ${app} answers.`,
      button: "Run again",
    };
  }
  const words = layerWords(shown.layers, shown.outcome, page.without_page, page.page_only);
  if (!words?.apiAlone || shown.outcome !== "failed") return null;
  const notShown = shown.steps.some((step) => step.page?.check?.outcome === "failed");
  if (notShown || page.shown_alone === "fail") {
    const part = shown.steps.map((step) => step.page?.part).find((found) => found?.missing);
    const lost = part
      ? `it didn't show “${part.missing}”`
      : notShown
        ? "it didn't show all of the reply"
        : "it didn't show what this test looks for";
    return {
      text: `The reply is right, so this is the page's to fix: ${lost}. Change ${app}'s page, then run this test again.`,
      button: "Run this test again",
    };
  }
  if (page.health?.outcome !== "failed") return null;
  const why = shown.reason ? `, but ${shown.reason}` : ", but the page had errors";
  return {
    text: `Every reply showed${why}. Fix that, then run this test again.`,
    button: "Run this test again",
  };
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
