import { Menu } from "@base-ui/react/menu";
import { ChevronDown, Ellipsis } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";

const ITEM =
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 outline-none select-none data-[highlighted]:bg-selection";

/** A quiet picker: the current choice as a button that opens the others, and any actions. */
export function Picker<T extends string>({
  label,
  value,
  options,
  onPick,
  actions = [],
  className,
}: {
  /** The accessible name of the picker. */
  label: string;
  value: ReactNode;
  options: { value: T; label: ReactNode }[];
  onPick: (value: T) => void;
  /** Shown under the options, after a hairline. */
  actions?: { label: ReactNode; onClick: () => void }[];
  className?: string;
}) {
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={label}
        className={cn(
          "flex min-w-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[13px] font-semibold hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring data-[popup-open]:bg-selection",
          className,
        )}
      >
        <span className="truncate">{value}</span>
        <ChevronDown className="size-3 shrink-0 opacity-60" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={4} align="start" className="z-50 outline-none">
          <Menu.Popup className="min-w-44 rounded-lg bg-popover p-1 text-[13px] text-popover-foreground shadow-lg ring-1 ring-separator outline-none">
            {options.map((option) => (
              <Menu.Item key={option.value} onClick={() => onPick(option.value)} className={ITEM}>
                {option.label}
              </Menu.Item>
            ))}
            {actions.length ? <Menu.Separator className="mx-1 my-1 h-px bg-separator" /> : null}
            {actions.map((action, index) => (
              <Menu.Item key={index} onClick={action.onClick} className={cn(ITEM, "text-ink-2")}>
                {action.label}
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** A quiet "more" button that opens a few actions. */
export function MoreMenu({
  label,
  actions,
  className,
}: {
  /** The accessible name of the button. */
  label: string;
  actions: { label: ReactNode; onClick: () => void }[];
  className?: string;
}) {
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={label}
        className={cn(
          "grid size-7 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring data-[popup-open]:bg-selection",
          className,
        )}
      >
        <Ellipsis className="size-4" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={4} align="end" className="z-50 outline-none">
          <Menu.Popup className="min-w-40 rounded-lg bg-popover p-1 text-[13px] text-popover-foreground shadow-lg ring-1 ring-separator outline-none">
            {actions.map((action, index) => (
              <Menu.Item key={index} onClick={action.onClick} className={ITEM}>
                {action.label}
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
