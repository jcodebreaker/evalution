import { MousePointerClick } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { PageOffer } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { learnPage } from "./PageReach";
import { useOpenEnvironment } from "./use-isolation";

/**
 * The offer to test an app through its page, made where the person already is (Connect's last
 * step, a Home card) when its code has a chat page and nothing of it is set up. "Set up UI
 * testing" hands the test writer the page to learn, or opens the Test environment page when the
 * copy it learns on comes first; "Not now" turns the offer down for good, on the server.
 */
export function UiOffer({
  target,
  onAsk,
  className,
}: {
  target: string;
  /** Hands the test writer a message; without it, the button opens the Test environment page. */
  onAsk?: (text: string) => void | Promise<void>;
  className?: string;
}) {
  const [offer, setOffer] = useState<PageOffer | null>(null);
  const [working, setWorking] = useState<"setup" | "dismiss" | null>(null);
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = useOpenEnvironment();
  const read = useCallback(async () => {
    try {
      setOffer(await api.pageOffer(target));
    } catch {
      setOffer(null);
    }
  }, [target]);
  useEffect(() => {
    setOffer(null);
    setAsked(false);
    void read();
  }, [read]);

  if (!offer?.offer) return null;
  const learn = offer.can_learn && onAsk;

  const setUp = async () => {
    if (!learn) {
      open?.();
      return;
    }
    setWorking("setup");
    setError(null);
    try {
      await onAsk(learnPage(target, null));
      setAsked(true);
    } catch (failure) {
      setError(errorText(failure, "Couldn't ask the agent."));
    }
    setWorking(null);
  };
  const notNow = async () => {
    setWorking("dismiss");
    setError(null);
    try {
      setOffer(await api.dismissPageOffer(target));
    } catch (failure) {
      setError(errorText(failure, "Couldn't save that."));
    }
    setWorking(null);
  };

  return (
    <section
      className={cn("flex flex-col gap-3 text-[13px]", className)}
      aria-label="UI testing"
      data-testid="ui-offer"
    >
      <h2 className="m-0 flex items-center gap-2 text-[15px] font-semibold">
        <MousePointerClick className="size-4 text-tint" aria-hidden />
        {target} has a page. Test it the way people use it?
      </h2>
      {asked ? (
        <p className="m-0 max-w-[68ch] text-ink-2">
          The agent is learning the page on evalution's copy. Approve its map on the Test
          environment page, and tests play through the page from the next run.
        </p>
      ) : (
        <>
          <p className="m-0 max-w-[68ch] text-ink-2">
            Tests can type each message into its page and check the reply shows, on evalution's
            copy, keeping a screenshot of every message and a video when a try fails.
          </p>
          {!offer.can_learn && offer.why_not ? (
            <p className="m-0 max-w-[68ch] text-[12.5px] text-ink-3">{offer.why_not}</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            {learn || open ? (
              <Button disabled={working !== null} onClick={() => void setUp()}>
                {working === "setup" ? <Spinner /> : null}
                Set up UI testing
              </Button>
            ) : null}
            <Button variant="ghost" disabled={working !== null} onClick={() => void notNow()}>
              Not now
            </Button>
          </div>
        </>
      )}
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
    </section>
  );
}
