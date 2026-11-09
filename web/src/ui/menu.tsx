import { Menu } from "@base-ui/react/menu";
import { ChevronDown, Ellipsis } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";

const ITEM =
  "flex cursor-default items-center gap-2 rounded-[6px] px-2 py-1.5 outline-none select-none data-[highlighted]:bg-sunk";

/** A menu's sheet: white on a hairline, lifted by one soft shadow. */
const POPUP =
  "rounded-[10px] bg-popover p-1 text-[14px] text-popover-foreground shadow-[0_12px_32px_-8px_rgba(20,22,43,0.18)] ring-1 ring-separator outline-none";

/** A quiet picker: the current choice as a button that opens the others, and any actions. */
export function Picker<T extends string>({
  label,
  value,
  options,
  onPick,
  actions = [],
  header,
  className,
}: {
  /** The accessible name of the picker. */
  label: string;
  value: ReactNode;
  options: { value: T; label: ReactNode }[];
  onPick: (value: T) => void;
  /** Shown under the options, after a hairline. */
  actions?: { label: ReactNode; onClick: () => void }[];
  /** A quiet line above the options, after which a hairline. */
  header?: ReactNode;
  className?: string;
}) {
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={label}
        className={cn(
          "flex min-w-0 items-center gap-1 rounded-[6px] px-1.5 py-0.5 text-[14px] font-semibold hover:bg-sunk focus-visible:outline-2 focus-visible:outline-tint data-[popup-open]:bg-sunk",
          className,
        )}
      >
        <span className="truncate">{value}</span>
        <ChevronDown className="size-3 shrink-0 opacity-60" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={4} align="start" className="z-50 outline-none">
          <Menu.Popup className={cn("min-w-44", POPUP)}>
            {header ? (
              <>
                <div className="truncate px-2 pt-1 pb-1.5 text-[12.5px] text-ink-3">{header}</div>
                <Menu.Separator className="mx-1 my-1 h-px bg-separator" />
              </>
            ) : null}
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
          "grid size-7 shrink-0 place-items-center rounded-[6px] text-ink-2 hover:bg-sunk focus-visible:outline-2 focus-visible:outline-tint data-[popup-open]:bg-sunk",
          className,
        )}
      >
        <Ellipsis className="size-4" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={4} align="end" className="z-50 outline-none">
          <Menu.Popup className={cn("min-w-40", POPUP)}>
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
