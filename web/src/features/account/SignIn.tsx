import { buttonClass } from "../../ui/button";
import { Logo } from "../shell/Logo";

/** Hosted, signed out: the product and one way in, back to where the person was going. */
export function SignIn() {
  const next = `${window.location.pathname}${window.location.search}`;
  return (
    <div className="canvas grid h-full place-items-center text-foreground">
      <div className="flex flex-col items-center gap-6">
        <span className="flex items-center gap-2.5">
          <Logo />
          <span className="text-[16px] font-semibold tracking-[-0.02em]">evalution</span>
        </span>
        <a
          href={`/auth/login?${new URLSearchParams({ next })}`}
          className={buttonClass("default", "lg")}
        >
          Sign in
        </a>
      </div>
    </div>
  );
}
