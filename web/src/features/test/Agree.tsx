import { ThumbsDown, ThumbsUp } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { LineAgreement } from "../../lib/schema";
import { UNRELIABLE } from "../../lib/verdict";
import { Button } from "../../ui/button";

/** The key that agrees with the grader on a focused line, and the one that disagrees. */
export const AGREE_KEYS: Record<string, boolean> = { a: true, d: false };

/**
 * Agree or not with the grader on a rubric line of this try (pressed again, the answer is
 * cleared), and how often it has agreed with you on the line.
 */
export function Agree({
  agreement,
  onPress,
}: {
  agreement: LineAgreement;
  onPress: (agree: boolean) => void;
}) {
  const { agreed, labelled, unreliable, mine, can } = agreement;
  if (!can && !labelled) return null;
  const choice = (agree: boolean, label: string, icon: ReactNode) => (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      aria-pressed={mine === agree}
      className={cn("text-ink-3", mine === agree && "bg-selection text-foreground")}
      onClick={() => onPress(agree)}
    >
      {icon}
    </Button>
  );
  return (
    <span className="mt-1 flex items-center gap-0.5 text-xs text-ink-3">
      {can ? choice(true, "Agree", <ThumbsUp />) : null}
      {can ? choice(false, "Disagree", <ThumbsDown />) : null}
      {labelled ? (
        <span className={cn("ms-1 tabular-nums", unreliable && "text-warning")}>
          {`${agreed}/${labelled}`}
          {unreliable ? ` · ${UNRELIABLE}` : ""}
        </span>
      ) : null}
    </span>
  );
}
