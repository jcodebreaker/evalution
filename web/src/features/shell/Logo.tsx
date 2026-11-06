/** The mark: a spider chart whose data shape is the bot. */
export function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className="size-7 shrink-0">
      <path
        d="M57.9 21.3 42.7 6.1H21.3L6.1 21.3v21.4l15.2 15.2h21.4l15.2-15.2z"
        fill="none"
        strokeWidth="1.5"
        strokeLinejoin="round"
        className="stroke-foreground opacity-30"
      />
      <path
        d="M6.1 21.3 57.9 42.7M57.9 21.3 6.1 42.7M21.3 6.1l21.4 51.8M42.7 6.1 21.3 57.9"
        fill="none"
        className="stroke-foreground opacity-20"
      />
      <path
        d="M54.2 22.8 37.7 18.1H26.3L9.8 22.8l3.7 16.9 13.1 5.2h10.8l13.1-5.2z"
        strokeWidth="2.5"
        strokeLinejoin="round"
        className="fill-tint stroke-tint"
      />
      <circle cx="26.5" cy="29.5" r="3.2" className="fill-primary-foreground" />
      <circle cx="37.5" cy="29.5" r="3.2" className="fill-primary-foreground" />
    </svg>
  );
}
