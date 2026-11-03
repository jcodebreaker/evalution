import { cn } from "../../lib/cn";

export type Mode = "plan" | "result";

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
        "h-7 rounded-[7px] px-3 text-[13px] font-medium text-ink-2 focus-visible:outline-2 focus-visible:outline-ring disabled:text-ink-3",
        value === mode && "bg-group text-foreground ring-[0.5px] ring-separator",
      )}
    >
      {label}
    </button>
  );
  return (
    <div className="inline-flex gap-0.5 rounded-[9px] bg-bubble p-[3px]">
      {option("plan", "What it checks")}
      {option("result", resultLabel, resultOff)}
    </div>
  );
}
