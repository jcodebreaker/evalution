import { Play } from "lucide-react";
import type { Compare } from "../../lib/schema";
import { pageNextStep } from "../../lib/verdict";
import { Button } from "../../ui/button";

/** A page try that didn't pass ends in one sentence and the one button that follows it. */
export function PageNextStep({
  shown,
  appName,
  going,
  onRun,
}: {
  shown: Compare;
  appName: string;
  going: boolean;
  onRun: () => void;
}) {
  const next = pageNextStep(shown, appName);
  if (!next) return null;
  return (
    <section
      className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-[12px] border border-tint-soft-2 bg-tint-soft px-5 py-4"
      aria-label="Next step"
      data-testid="next-step"
    >
      <div className="min-w-0 flex-1">
        <b className="block text-[14px] font-semibold">Next step</b>
        <p className="m-0 mt-0.5 text-[14px]">{next.text}</p>
      </div>
      <Button disabled={going} onClick={onRun}>
        <Play />
        {next.button}
      </Button>
    </section>
  );
}
