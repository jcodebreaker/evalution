/** evalution's mark (public/favicon.svg): the bot, in the purple, its eyes the page's white. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className={className ?? "size-6 shrink-0"}>
      <path
        d="M58 21 40 11H24L6 21l5 22 14 11h14l14-11z"
        strokeWidth="3"
        strokeLinejoin="round"
        className="fill-tint stroke-tint"
      />
      <circle cx="24" cy="29" r="5.5" className="fill-group" />
      <circle cx="40" cy="29" r="5.5" className="fill-group" />
    </svg>
  );
}
