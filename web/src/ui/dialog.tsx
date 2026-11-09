import { AlertDialog as Alert } from "@base-ui/react/alert-dialog";
import { Dialog } from "@base-ui/react/dialog";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import { buttonClass } from "./button";

const BACKDROP =
  "fixed inset-0 z-40 bg-overlay backdrop-blur-[2px] transition-opacity duration-150 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0";

/** A dialog's sheet: white, radius 14, lifted off the page by its one shadow. */
const POPUP =
  "rounded-[14px] bg-group text-foreground shadow-[0_0_0_1px_var(--separator),0_24px_60px_-12px_rgba(20,22,43,0.22)] transition-[opacity,scale] duration-150 outline-none data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0";

/** A small centred confirm, as shadcn's AlertDialog. */
export function ConfirmDialog({
  onClose,
  title,
  description,
  children,
  footer,
  wide = false,
}: {
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer: ReactNode;
  /** Room for more than a sentence or two: settings, groups, a longer button. */
  wide?: boolean;
}) {
  return (
    <Alert.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Alert.Portal>
        <Alert.Backdrop className={BACKDROP} />
        <Alert.Popup
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex -translate-1/2 flex-col gap-4 pt-5",
            wide
              ? "max-h-[calc(100vh-32px)] w-[min(620px,calc(100vw-32px))] overflow-y-auto"
              : "w-[min(400px,calc(100vw-32px))] overflow-hidden",
            POPUP,
          )}
        >
          <div className="flex flex-col gap-1 px-6">
            <Alert.Title className="m-0 text-[16px] leading-snug font-semibold">
              {title}
            </Alert.Title>
            {description ? (
              <Alert.Description className="m-0 text-[13px] text-ink-2 tabular-nums">
                {description}
              </Alert.Description>
            ) : null}
          </div>
          {children ? <div className="flex flex-col gap-4 px-6">{children}</div> : null}
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-separator bg-sunk px-6 py-3.5">
            {footer}
          </div>
        </Alert.Popup>
      </Alert.Portal>
    </Alert.Root>
  );
}

/** One picture, large and centred, with what it shows as its title. */
export function PictureDialog({
  onClose,
  title,
  src,
  alt,
}: {
  onClose: () => void;
  title: ReactNode;
  src: string;
  alt: string;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className={BACKDROP} />
        <Dialog.Popup
          className={cn(
            "fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100vh-32px)] w-[min(1100px,calc(100vw-32px))] -translate-1/2 flex-col gap-3 p-4",
            POPUP,
          )}
        >
          <div className="flex items-center gap-2">
            <Dialog.Title className="m-0 flex-1 text-[15px] font-semibold">{title}</Dialog.Title>
            <Dialog.Close className={buttonClass("ghost", "sm")}>Close</Dialog.Close>
          </div>
          <img
            src={src}
            alt={alt}
            className="min-h-0 w-full rounded-[10px] object-contain ring-1 ring-separator"
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** A panel from the end edge, as shadcn's Sheet. */
export function Sheet({
  onClose,
  title,
  children,
  footer,
}: {
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className={BACKDROP} />
        <Dialog.Popup
          className={cn(
            "fixed inset-y-0 end-0 z-50 flex w-full flex-col border-s border-separator bg-group shadow-[0_0_60px_-12px_rgba(20,22,43,0.22)] outline-none sm:max-w-[600px]",
            "transition-transform duration-200 data-[ending-style]:translate-x-full data-[starting-style]:translate-x-full",
          )}
        >
          <Dialog.Title className="m-0 px-5 pt-5 pb-3 text-[16px] font-semibold">
            {title}
          </Dialog.Title>
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-5 pb-5">
            {children}
          </div>
          <div className="flex justify-end gap-2 border-t border-separator bg-sunk px-5 py-3.5">
            {footer}
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
