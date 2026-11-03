const RECENT_KEY = "evalution.folders";

const RECENT_MAX = 5;

export function recentFolders(): string[] {
  try {
    const found: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(found) ? found.filter((f): f is string => typeof f === "string") : [];
  } catch {
    return [];
  }
}

export function rememberFolder(folder: string) {
  try {
    const next = [folder, ...recentFolders().filter((f) => f !== folder)].slice(0, RECENT_MAX);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // Without storage, recent folders are not kept.
  }
}

export const lastPart = (folder: string) => folder.replace(/\/+$/, "").split("/").pop() || folder;
