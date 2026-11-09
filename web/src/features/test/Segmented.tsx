import { cn } from "../../lib/cn";

export type Mode = "plan" | "result";

/** The test's two views as tabs on a line: what it checks, and its latest result. */
export function Segmented({
  value,
  onChange,
  resultLabel,
  resultOff,
}: {
  value: Mode;
  onChange: (mode: Mode) => void;
  resultLabel: string;
  resultOff: boolean;
}) {
  const option = (mode: Mode, label: string, disabled = false) => (
    <button
      type="button"
      aria-pressed={value === mode}
      disabled={disabled}
      onClick={() => onChange(mode)}
      className={cn(
        "-mb-px h-9 border-b-2 border-transparent px-2 text-[14px] font-medium text-ink-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-ring disabled:text-ink-3 disabled:hover:text-ink-3",
        value === mode && "border-tint text-tint-ink hover:text-tint-ink",
      )}
    >
      {label}
    </button>
  );
  return (
    <div className="flex gap-1 border-b border-separator">
      {option("plan", "What it checks")}
      {option("result", resultLabel, resultOff)}
    </div>
  );
}
