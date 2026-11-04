import type { ReactNode } from "react";
import { cn } from "../lib/cn";

// The Markdown the test writer writes, as React elements (never as HTML): paragraphs, headings,
// lists, quotes, code, bold, italics and links.

const INLINE =
  /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|\[[^\]]+\]\([^)\s]+\))/;

function inline(text: string): ReactNode[] {
  return text
    .split(INLINE)
    .filter(Boolean)
    .map((part, index) => {
      if (part.startsWith("`") && part.endsWith("`") && part.length > 1)
        return (
          <code key={index} className="rounded-[5px] bg-bubble px-1 py-px font-mono text-[0.9em]">
            {part.slice(1, -1)}
          </code>
        );
      if (/^(\*\*|__).+\1$/.test(part))
        return <strong key={index}>{inline(part.slice(2, -2))}</strong>;
      if (/^([*_]).+\1$/.test(part)) return <em key={index}>{inline(part.slice(1, -1))}</em>;
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
      if (link) {
        const href = link[2] ?? "";
        if (!/^https?:\/\//.test(href)) return link[1];
        return (
          <a
            key={index}
            href={href}
            target="_blank"
            rel="noreferrer"
            className="underline decoration-ink-3 underline-offset-2"
          >
            {link[1]}
          </a>
        );
      }
      return part;
    });
}

type Block =
  | { kind: "p" | "quote"; lines: string[] }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "code"; text: string }
  | { kind: "table"; head: string[]; rows: string[][] };

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
const RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function blocks(source: string): Block[] {
  const out: Block[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  for (let at = 0; at < lines.length; at++) {
    const line = lines[at] ?? "";
    const last = out.at(-1);
    if (line.startsWith("```")) {
      const code: string[] = [];
      for (at++; at < lines.length && !(lines[at] ?? "").startsWith("```"); at++) {
        code.push(lines[at] ?? "");
      }
      out.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    if (!line.trim()) {
      out.push({ kind: "p", lines: [] });
      continue;
    }
    if (line.includes("|") && RULE.test(lines[at + 1] ?? "")) {
      const rows: string[][] = [];
      for (at += 2; at < lines.length && (lines[at] ?? "").includes("|"); at++) {
        rows.push(cells(lines[at] ?? ""));
      }
      at--;
      out.push({ kind: "table", head: cells(line), rows });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      out.push({ kind: "h", level: heading[1]?.length ?? 1, text: heading[2] ?? "" });
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const number = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet ?? number;
    if (item) {
      const kind = bullet ? "ul" : "ol";
      if (last?.kind === kind) last.items.push(item[1] ?? "");
      else out.push({ kind, items: [item[1] ?? ""] });
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      if (last?.kind === "quote") last.lines.push(quote[1] ?? "");
      else out.push({ kind: "quote", lines: [quote[1] ?? ""] });
      continue;
    }
    // A line under a list item continues it; else it continues the paragraph.
    if ((last?.kind === "ul" || last?.kind === "ol") && /^\s+/.test(line)) {
      last.items[last.items.length - 1] += ` ${line.trim()}`;
    } else if (last?.kind === "p" && last.lines.length) last.lines.push(line);
    else out.push({ kind: "p", lines: [line] });
  }
  return out.filter((block) => block.kind !== "p" || block.lines.length);
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-2 break-words", className)}>
      {blocks(text).map((block, index) => {
        switch (block.kind) {
          case "h":
            return (
              <p key={index} className={cn("m-0 font-semibold", block.level <= 2 && "text-[15px]")}>
                {inline(block.text)}
              </p>
            );
          case "ul":
          case "ol": {
            const List = block.kind;
            return (
              <List
                key={index}
                className={cn(
                  "m-0 flex flex-col gap-1 ps-5",
                  block.kind === "ul" ? "list-disc" : "list-decimal",
                )}
              >
                {block.items.map((entry, at) => (
                  <li key={at}>{inline(entry)}</li>
                ))}
              </List>
            );
          }
          case "table":
            return (
              <div key={index} className="overflow-x-auto rounded-lg border border-separator">
                <table className="w-full border-collapse text-[12.5px]">
                  <thead>
                    <tr className="bg-head text-ink-2">
                      {block.head.map((cell, at) => (
                        <th key={at} scope="col" className="px-2.5 py-1.5 text-start font-medium">
                          {inline(cell)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, at) => (
                      <tr key={at} className="border-t border-separator">
                        {row.map((cell, column) => (
                          <td key={column} className="px-2.5 py-1.5 tabular-nums">
                            {inline(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "quote":
            return (
              <blockquote key={index} className="m-0 border-s-2 border-separator ps-3 text-ink-2">
                {inline(block.lines.join(" "))}
              </blockquote>
            );
          case "code":
            return (
              <pre
                key={index}
                className="m-0 overflow-x-auto rounded-[7px] bg-window px-2.5 py-2 font-mono text-[12px] leading-[1.55]"
              >
                {block.text}
              </pre>
            );
          default:
            return (
              <p key={index} className="m-0">
                {block.lines.map((line, at) => (
                  <span key={at}>
                    {at ? <br /> : null}
                    {inline(line)}
                  </span>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}
