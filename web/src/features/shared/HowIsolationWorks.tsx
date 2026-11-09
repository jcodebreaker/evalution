import { useState } from "react";
import { Button } from "../../ui/button";
import { Sheet } from "../../ui/dialog";
import { TextLink } from "./group";

/** How evalution keeps tries apart, one sentence a step. */
export const HOW_STEPS: readonly [string, string][] = [
  [
    "Find where the app keeps things",
    "evalution starts its own copy of your app, sends it one probe message, and watches where that message ends up: databases, volumes, folders.",
  ],
  [
    "Make a plan",
    "For each of those places it writes down how to put it back: a fresh copy, or the starting state kept aside.",
  ],
  [
    "Prove it",
    "A rehearsal plants a marker in the copy, puts everything back, and must find the marker gone; if it turns up anywhere the plan misses, the plan isn't proven.",
  ],
  [
    "Every try",
    "Before each try, every place is put back and checked against the starting state; a try whose check fails isn't judged.",
  ],
  [
    "Keep current",
    "When a commit changes the code the plan rests on, the plan is marked out of date, and the next run proves it again first.",
  ],
];

/** What no plan can put back. */
export const CANT_RESET: readonly string[] = [
  "Memory kept by a hosted service, outside your computer: there, each try can only be a new person.",
  "What the app holds in its own memory while it runs, unless the plan starts it again before each try.",
];

/** The explainer itself: five steps, then what it can't reset. */
export function HowIsolationWorks() {
  return (
    <div className="flex flex-col gap-4 text-[14px]">
      <p className="m-0 text-ink-2">
        Each try should start from the same place, so a result says something about your app and not
        about what an earlier try left behind. Your own app is never touched: all of this happens on
        evalution's copy.
      </p>
      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        {HOW_STEPS.map(([title, text], index) => (
          <li key={title} className="grid grid-cols-[22px_minmax(0,1fr)] gap-2.5">
            <span className="grid size-[22px] place-items-center rounded-full bg-bubble text-[11.5px] font-semibold text-ink-2">
              {index + 1}
            </span>
            <div className="flex flex-col gap-0.5">
              <span className="font-semibold">{title}</span>
              <span className="text-ink-2">{text}</span>
            </div>
          </li>
        ))}
      </ol>
      <div className="flex flex-col gap-1.5">
        <h3 className="m-0 text-[14px] font-semibold">What it can't reset</h3>
        <ul className="m-0 flex flex-col gap-1 ps-4 text-ink-2">
          {CANT_RESET.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** "How this works": a link that opens the explainer beside the page. */
export function HowIsolationLink({ label = "How this works" }: { label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <TextLink onClick={() => setOpen(true)}>{label}</TextLink>
      {open ? (
        <Sheet
          onClose={() => setOpen(false)}
          title="How isolation works"
          footer={
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Close
            </Button>
          }
        >
          <HowIsolationWorks />
        </Sheet>
      ) : null}
    </>
  );
}
