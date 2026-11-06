import { useCallback, useEffect, useState } from "react";

// Where the page is, kept in the URL's query so a screen can be linked and Back works.

export type View = "home" | "app" | "tests" | "benchmarks" | "runs" | "connect" | "model";

export type Place = {
  target: string | null;
  set: string | null;
  view: View;
  run: string | null;
  case: string | null;
  base: string | null;
};

const VIEWS: View[] = ["home", "app", "tests", "benchmarks", "runs", "connect", "model"];
const KEYS = ["target", "set", "view", "run", "case", "base"] as const;
/** Places links from before the App screen still name. */
const OLD: Record<string, View> = {
  understanding: "app",
  profile: "app",
  settings: "app",
  agent: "tests",
};
/** The first screen, left out of the URL. */
const HOME: View = "home";

function read(): Place {
  const params = new URLSearchParams(window.location.search);
  const asked = params.get("view") ?? "";
  const view = OLD[asked] ?? VIEWS.find((entry) => entry === asked) ?? HOME;
  return {
    target: params.get("target"),
    set: params.get("set"),
    view,
    run: params.get("run"),
    case: params.get("case"),
    base: params.get("base"),
  };
}

/**
 * Hosted, the workspace the page works in: kept in the URL with the rest of where the page is, so a
 * link to a run opens in its workspace. Read at each request; changed only by loading the page.
 */
export const currentWorkspace = () => new URLSearchParams(window.location.search).get("workspace");

/** The page in `workspace`, loaded afresh so nothing read in another one stays on screen. */
export function openWorkspace(workspace: string) {
  window.location.assign(`/?${new URLSearchParams({ workspace })}`);
}

function write(place: Place, replace: boolean) {
  const params = new URLSearchParams();
  for (const key of KEYS) {
    const value = place[key];
    if (value && !(key === "view" && value === HOME)) params.set(key, value);
  }
  const workspace = currentWorkspace();
  if (workspace) params.set("workspace", workspace);
  const search = params.toString();
  const url = `${window.location.pathname}${search ? `?${search}` : ""}`;
  if (replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
}

export function usePlace() {
  const [place, setPlace] = useState<Place>(read);
  useEffect(() => {
    const onPop = () => setPlace(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  /** Moves the page; `replace` keeps it out of the history (a default filled in). */
  const go = useCallback((next: Partial<Place>, replace = false) => {
    setPlace((now) => {
      const merged = { ...now, ...next };
      write(merged, replace);
      return merged;
    });
  }, []);
  return [place, go] as const;
}
