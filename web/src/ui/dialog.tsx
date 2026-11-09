import { AlertDialog as Alert } from "@base-ui/react/alert-dialog";
import { Dialog } from "@base-ui/react/dialog";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";

const BACKDROP =
  "fixed inset-0 z-40 bg-overlay transition-opacity duration-150 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0";

/** A small centred confirm, as shadcn's AlertDialog. */
export function ConfirmDialog({
  onClose,
  title,
  description,
  children,
  footer,
}: {
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer: ReactNode;
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
        <Alert.Popup className="fixed top-1/2 left-1/2 z-50 flex w-[min(360px,calc(100vw-32px))] -translate-1/2 flex-col gap-4 rounded-xl bg-window p-5 shadow-[0_0_0_0.5px_var(--separator),0_24px_60px_-12px_rgba(0,0,0,0.35)] transition-[opacity,scale] duration-150 outline-none data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
          <div className="flex flex-col gap-1 text-center">
            <Alert.Title className="m-0 text-[14px] font-semibold">{title}</Alert.Title>
            {description ? (
              <Alert.Description className="m-0 text-[13px] text-ink-2 tabular-nums">
                {description}
              </Alert.Description>
            ) : null}
          </div>
          {children}
          <div className="grid grid-cols-2 gap-2">{footer}</div>
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
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100vh-32px)] w-[min(1100px,calc(100vw-32px))] -translate-1/2 flex-col gap-3 rounded-xl bg-window p-4 shadow-[0_0_0_0.5px_var(--separator),0_24px_60px_-12px_rgba(0,0,0,0.35)] transition-[opacity,scale] duration-150 outline-none data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
          <div className="flex items-center gap-2">
            <Dialog.Title className="m-0 flex-1 text-[14px] font-semibold">{title}</Dialog.Title>
            <Dialog.Close className="h-7 rounded-md px-2.5 text-[13px] text-ink-2 hover:bg-selection hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
              Close
            </Dialog.Close>
          </div>
          <img
            src={src}
            alt={alt}
            className="min-h-0 w-full rounded-lg object-contain ring-[0.5px] ring-separator"
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
            "fixed inset-y-0 end-0 z-50 flex w-full flex-col bg-window shadow-xl outline-none sm:max-w-[600px]",
            "transition-transform duration-200 data-[ending-style]:translate-x-full data-[starting-style]:translate-x-full",
          )}
        >
          <Dialog.Title className="m-0 px-4 pt-4 pb-3 text-[15px] font-semibold">
            {title}
          </Dialog.Title>
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto px-4 pb-4">
            {children}
          </div>
          <div className="flex justify-end gap-2 border-t border-separator px-4 py-3">{footer}</div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
