import { Check, CircleAlert } from "lucide-react";
import { Spinner } from "../../ui/loading";

export function Steps({
  steps,
}: {
  steps: { text: string; state: "doing" | "done" | "problem" }[];
}) {
  return (
    <ol className="m-0 flex list-none flex-col gap-2.5 p-0 text-[13.5px]">
      {steps.map((step) => (
        <li key={step.text} className="flex items-start gap-2.5">
          <span className="mt-[3px] grid size-3.5 shrink-0 place-items-center">
            {step.state === "doing" ? <Spinner /> : null}
            {step.state === "done" ? (
              <Check className="size-3.5 text-success" aria-label="Done" />
            ) : null}
            {step.state === "problem" ? (
              <CircleAlert className="size-3.5 text-warning" aria-label="Needs attention" />
            ) : null}
          </span>
          <span className={step.state === "doing" ? "shimmer" : undefined}>{step.text}</span>
        </li>
      ))}
    </ol>
  );
}
