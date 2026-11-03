import type { KeyboardEvent } from "react";
import { useState } from "react";
import { errorText } from "../../lib/api";

/** Asks the test writer and shows its errors in words a person may read. */
export function useAsk(ask: (text: string) => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return false;
    setBusy(true);
    setError(null);
    try {
      await ask(trimmed);
      return true;
    } catch (failure) {
      setError(errorText(failure, "Couldn't reach the test writer."));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, send };
}

/** Enter sends, Shift+Enter starts a new line. */
export function onEnter(send: () => void) {
  return (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  };
}

export const TEXTAREA =
  "field-sizing-content min-h-12 w-full resize-none border-0 bg-transparent p-0 text-[15px] leading-[1.45] outline-none placeholder:text-ink-3";
