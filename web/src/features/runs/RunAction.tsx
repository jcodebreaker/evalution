import { Play } from "lucide-react";
import { Button } from "../../ui/button";

/** A test's quiet run action, shown on hover or focus of its row (always on touch widths). */
export function RunAction({ name, onRun }: { name: string; onRun: () => void }) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={`Run ${name}`}
      onClick={(event) => {
        event.stopPropagation();
        onRun();
      }}
      className="shrink-0 text-ink-3 opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 max-md:opacity-100"
    >
      <Play />
    </Button>
  );
}
