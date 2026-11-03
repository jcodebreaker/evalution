/** What a form says when a stored key won't go along to a new address. */
export const KEY_LEFT_BEHIND = "The saved key isn't sent to a new address. Type it again.";

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

/** The hosts *options* call, as the server reads them: every address at any depth, but what's sent
 * as content. */
export function hostsOf(options: unknown): Set<string> {
  const found = new Set<string>();
  if (Array.isArray(options)) {
    for (const item of options) for (const host of hostsOf(item)) found.add(host);
  } else if (options && typeof options === "object") {
    for (const [key, value] of Object.entries(options)) {
      if (typeof value === "string" && (key === "health" || key.endsWith("url"))) {
        found.add(hostOf(value));
      } else if (key !== "body" && key !== "headers") {
        for (const host of hostsOf(value)) found.add(host);
      }
    }
  }
  return found;
}
