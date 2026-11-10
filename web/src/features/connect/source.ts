/** What the connect field names: a folder, a repository on GitHub, one of its pull requests, a
 * branch, tag or commit of it, or an app's address (docs/versions.md "Connect by address"). */
export type SourceKind = "folder" | "repo" | "pull" | "ref" | "address";

export const SOURCE_WORDS: Record<SourceKind, string> = {
  folder: "Folder",
  repo: "GitHub repository",
  pull: "Pull request",
  ref: "Branch, tag or commit",
  address: "Address",
};

const SLUG = "[A-Za-z0-9_.-]+";
const GITHUB = new RegExp(`^(https?://)?(www\\.)?github\\.com/${SLUG}/${SLUG}`, "i");
const SHORT = new RegExp(`^${SLUG}/${SLUG}(\\.git)?$`);

/** What the field's text names, as the server reads it; a bare `owner/repo` is a repository. */
export function sourceKind(text: string): SourceKind {
  const said = text.trim();
  if (!said) return "folder";
  if (GITHUB.test(said)) {
    if (/\/pull\/\d+/.test(said) || /#\d+$/.test(said)) return "pull";
    if (/@[^/]+$/.test(said) || /\/tree\//.test(said)) return "ref";
    return "repo";
  }
  if (/^https?:\/\//i.test(said)) return "address";
  if (/^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/i.test(said)) return "address";
  if (/^[\w.-]+:\d+(\/|$)/.test(said)) return "address";
  if (/^[~./]/.test(said)) return "folder";
  const [base, after] = said.split(/[#@]/, 2) as [string, string | undefined];
  if (SHORT.test(base)) {
    if (said.includes("#") && /^\d+$/.test(after ?? "")) return "pull";
    if (said.includes("@") && after) return "ref";
    if (after === undefined) return "repo";
  }
  return "folder";
}

/** The examples under the field: one of each thing it takes. */
export const EXAMPLES: readonly { text: string; fill: string }[] = [
  { text: "~/code/my-app", fill: "~/code/my-app" },
  { text: "owner/repo", fill: "owner/repo" },
  { text: "owner/repo#412", fill: "owner/repo#412" },
  { text: "localhost:3000", fill: "http://localhost:3000" },
];
