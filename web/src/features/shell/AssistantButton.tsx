import { Logo } from "./Logo";

/**
 * The assistant: the mark at the bottom end of every screen, breathing gently while it waits (still,
 * for those who ask for less motion). It opens the panel about what is on screen.
 */
export function AssistantButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label="Ask evalution"
      title="Ask evalution"
      onClick={onClick}
      className="assistant-orb fixed end-7 bottom-6 z-20 grid size-16 place-items-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring max-md:end-4 max-md:bottom-4"
    >
      <span aria-hidden className="assistant-halo absolute -inset-1.5 rounded-full" />
      <span className="assistant-mark relative grid">
        <Logo className="size-16" />
      </span>
    </button>
  );
}
