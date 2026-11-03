import type { ReactNode } from "react";

export const SECTION = "border-separator pt-1.5 pb-2.5 not-last:border-b";

export function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mx-[18px] mt-2.5 mb-1 text-[11.5px] font-semibold tracking-[0.02em] text-ink-3 uppercase">
      {children}
    </h3>
  );
}
