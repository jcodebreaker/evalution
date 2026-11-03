import { useState } from "react";
import { api, errorText } from "../../lib/api";
import type { TargetBody, TargetCheck } from "../../lib/schema";

/** Check, whose results show under the form. */
export function useCheck() {
  const [check, setCheck] = useState<TargetCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (name: string, body?: TargetBody) => {
    setChecking(true);
    setError(null);
    try {
      setCheck(await api.reach(name, body));
    } catch (failure) {
      setCheck(null);
      setError(errorText(failure, "Couldn't check the connection."));
    }
    setChecking(false);
  };
  return { check, checking, error, run };
}
