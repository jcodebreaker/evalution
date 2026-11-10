import { count, seconds } from "../../lib/format";
import type { Compare, Layers, PlanStep, StepPage, TryOutcome } from "../../lib/schema";
import { LAYER_NAME, LAYER_SAID, layerWords, notTakenOf, shownWords } from "../../lib/verdict";
import type { Outcome } from "../../ui/status";

// A try in the few words its page says it in: its facts, where it broke, and each message's turn.

type Layer = Layers["stored"];

const OUTCOME: Record<Layer, Outcome> = {
  pass: "pass",
  fail: "fail",
  unjudged: "unjudged",
  absent: "none",
};
const OF_TRY: Record<TryOutcome, Layer> = { passed: "pass", failed: "fail", unjudged: "unjudged" };

/** An outcome of a try or a check, as the one mark. */
export const markOf = (outcome: TryOutcome | null | undefined): Outcome =>
  outcome ? OUTCOME[OF_TRY[outcome]] : "none";

const capital = (text: string) => {
  const said = text.trim().replace(/\.$/, "");
  return said.charAt(0).toUpperCase() + said.slice(1);
};

/** A fact in a page's header; `tint`: how it played, when through the page. */
export type Fact = { text: string; tint?: boolean };

/** How a test or a try plays, in a few words. */
export function playsFact(plays: "api" | "page" | "page_only"): Fact {
  if (plays === "api") return { text: "Through the API" };
  return { text: plays === "page" ? "Through the page" : "Through its page alone", tint: true };
}

/** The try's facts for its header: how long it took, how many messages, and how it played. */
export function tryFacts(shown: Pick<Compare, "seconds" | "steps" | "page">): Fact[] {
  const said = shown.steps.filter((step) => step.kind === "say").length;
  const page = shown.page;
  return [
    shown.seconds === null ? null : { text: seconds(shown.seconds, shown.seconds < 10) },
    said ? { text: count(said, "message") } : null,
    playsFact(page ? (page.page_only ? "page_only" : "page") : "api"),
  ].filter((fact): fact is Fact => !!fact);
}

/** The test's messages, each with its count from 1. */
export function turnsOf(steps: readonly PlanStep[]): { step: PlanStep; n: number }[] {
  return steps.filter((step) => step.kind === "say").map((step, index) => ({ step, n: index + 1 }));
}

/** One layer of the try in the "Where it broke" strip: its mark, its name and one plain line. */
export type BreakCell = {
  layer: keyof Layers | "healthy";
  name: string;
  state: Outcome;
  /** Said to a screen reader and on hover: `Shown: failed`. */
  label: string;
  line: string;
};

/** The first check of the try of these kinds that came out so, in the server's words. */
function checkText(
  shown: Pick<Compare, "explain">,
  kinds: readonly string[],
  state: Layer,
): string | null {
  const outcome = { pass: "passed", fail: "failed", unjudged: "unjudged", absent: null }[state];
  const found = shown.explain?.code.checks.find(
    (check) => check.outcome === outcome && kinds.includes(check.kind ?? ""),
  );
  return found ? capital(found.text) : null;
}

const ANSWER_KINDS = ["reply_text", "reply_lacks", "context", "turn", "timeout"];

/** What showed of the replies, in a line: the first message that didn't, or that all did. */
function shownLine(shown: Compare, state: Layer): string {
  if (state === "pass") return "Every reply showed";
  const turns = turnsOf(shown.steps).filter(({ step }) => step.page);
  const wanted = state === "fail" ? "failed" : "unjudged";
  const at = turns.find(({ step }) => step.page?.check?.outcome === wanted);
  const page = at?.step.page;
  if (at && page && state === "fail") {
    if (page.part?.shown) return `Turn ${at.n}'s reply showed only in part`;
    if ((page.shown ?? page.instead).trim()) return `Turn ${at.n} showed something else`;
    return `Turn ${at.n}'s reply never showed`;
  }
  if (page?.check?.why) return capital(page.check.why);
  return (
    checkText(shown, ["shown"], state) ??
    (state === "fail" ? "A reply didn't show" : "Couldn't judge what showed")
  );
}

/** The page's health, in a line: how many errors and failed requests, or that there were none. */
function healthLine(shown: Compare, state: Layer): string {
  const page = shown.page;
  if (state === "pass") return "No errors, no failed requests";
  if (!page) return "Couldn't judge";
  const errors = page.console.filter((line) => line.level === "error").length;
  const requests = page.failed_requests.length;
  const said = [
    errors ? count(errors, "console error") : null,
    requests ? count(requests, "failed request") : null,
  ].filter(Boolean);
  if (state === "fail" && said.length) return capital(said.join(", "));
  return page.health?.why ? capital(page.health.why) : "Couldn't judge";
}

function lineOf(shown: Compare, layer: BreakCell["layer"], state: Layer): string {
  if (state === "absent") return "Nothing to check";
  switch (layer) {
    case "stored":
      return (
        checkText(shown, ["state"], state) ??
        (state === "pass"
          ? "What it stored is right"
          : state === "fail"
            ? "What it stored is wrong"
            : "Couldn't judge what it stored")
      );
    case "answered": {
      const notTaken = state === "pass" ? null : notTakenOf(shown);
      if (notTaken) {
        if (notTaken.silent) return "Never answered the page's message";
        return notTaken.status === null
          ? "Didn't take the page's message"
          : `Refused the page's message (${notTaken.status})`;
      }
      if (state === "pass") return "The reply is right";
      return (
        checkText(shown, ANSWER_KINDS, state) ??
        (state === "fail" ? "The reply is wrong" : "Couldn't judge the reply")
      );
    }
    case "shown":
      return shownLine(shown, state);
    default:
      return healthLine(shown, state);
  }
}

/**
 * Where the try broke: Stored, Answered, Shown and Page healthy, each with its mark and one plain
 * line. A try played through the page has every layer, one with nothing to check said so; a try
 * through the API only the layers it has. `null` when the server doesn't say its layers.
 */
export function breakCells(shown: Compare): BreakCell[] | null {
  const layers = shown.layers;
  if (!layers) return null;
  const page = shown.page;
  const cell = (layer: BreakCell["layer"], state: Layer): BreakCell => {
    const name = LAYER_NAME[layer === "healthy" ? "page_health" : layer];
    return {
      layer,
      name,
      state: OUTCOME[state],
      label: `${name}: ${state === "absent" ? "nothing to check" : LAYER_SAID[state]}`,
      line: lineOf(shown, layer, state),
    };
  };
  const words =
    page && layers.shown !== "absent"
      ? layerWords(layers, shown.outcome, page.without_page, page.page_only, notTakenOf(shown), {
          healthy: page.health?.outcome ?? null,
          shownAlone: page.shown_alone,
        })
      : null;
  if (!words) {
    const cells = (["stored", "answered"] as const)
      .filter((layer) => layers[layer] !== "absent")
      .map((layer) => cell(layer, layers[layer]));
    return cells.length ? cells : null;
  }
  const said = (layer: BreakCell["layer"]) =>
    words.pills.find((pill) => pill.layer === layer)?.state;
  const cells = (["stored", "answered", "shown"] as const).map((layer) =>
    cell(layer, said(layer) ?? "absent"),
  );
  const healthy = said("healthy");
  return healthy ? [...cells, cell("healthy", healthy)] : cells;
}

/** A message's turn on the page, in a few words with its mark: `Shown at 0.3 s`, `Not shown`. */
export function turnShown(page: StepPage): { state: Outcome; text: string } {
  const said = shownWords(page);
  if (said.held === false) {
    return { state: "fail", text: page.part?.shown ? "Shown only in part" : "Not shown" };
  }
  if (page.check?.outcome === "unjudged") return { state: "unjudged", text: "Nothing to show" };
  const at = page.shown_at === null ? "" : ` at ${seconds(page.shown_at, true)}`;
  return { state: said.held ? "pass" : "none", text: `Shown${at}` };
}

/** `text` split around the first place `words` appear in it, to mark them; `null` if they don't. */
export function splitAt(
  text: string,
  words: string,
): { before: string; words: string; after: string } | null {
  const at = words.trim() ? text.indexOf(words) : -1;
  if (at < 0) return null;
  return { before: text.slice(0, at), words, after: text.slice(at + words.length) };
}

/** The frame of a page try shown first: the message the try is read by, else the video. */
export function firstFrame(steps: readonly PlanStep[], video: boolean): number | "video" | null {
  const turns = turnsOf(steps).filter(({ step }) => step.page);
  const failed = turns.find(({ step }) => step.page?.check?.outcome === "failed");
  const last = turns[turns.length - 1];
  return failed?.n ?? last?.n ?? (video ? "video" : null);
}
