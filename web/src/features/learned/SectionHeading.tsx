import type { ReactNode } from "react";

export const SECTION = "border-separator pt-1.5 pb-2.5 not-last:border-b";

export function SectionHeading({ children }: { children: ReactNode }) {
  return <h3 className="mx-[18px] mt-3 mb-1 text-[13px] font-medium text-ink-2">{children}</h3>;
}
