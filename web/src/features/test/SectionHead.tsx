export function SectionHead({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-baseline gap-2">
      <h2 className="m-0 text-[16px] font-semibold">{title}</h2>
      <span className="text-[13px] text-ink-3">{sub}</span>
    </div>
  );
}
