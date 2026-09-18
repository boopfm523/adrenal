import type React from "react";

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*#{1,6}\s+/;
const RULE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const TABLE_ROW = /^\s*\|/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const INLINE = /(\*\*[^*\n]+?\*\*|`[^`\n]+`|\*[^*\s](?:[^*\n]*[^*\s])?\*)/g;

type LineKind = "table" | "bullet" | "numbered" | "single" | "text";

function lineKind(line: string): LineKind {
  if (TABLE_ROW.test(line)) return "table";
  if (RULE.test(line) || HEADING.test(line)) return "single";
  if (BULLET.test(line)) return "bullet";
  if (NUMBERED.test(line)) return "numbered";
  return "text";
}

function inline(text: string): React.ReactNode[] {
  return text
    .split(INLINE)
    .filter((part) => part !== "")
    .map((part, index) => {
      if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
      if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
      if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) return <em key={index}>{part.slice(1, -1)}</em>;
      return part;
    });
}

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function table(lines: string[], key: number): React.JSX.Element | null {
  if (lines.length < 2 || !TABLE_SEPARATOR.test(lines[1] ?? "")) return null;
  const header = cells(lines[0] ?? "");
  const rows = lines.slice(2).map(cells);
  return (
    <div key={key} className="chat-answer-table">
      <table>
        <thead><tr>{header.map((cell, index) => <th key={index} scope="col">{inline(cell)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{header.map((_, index) => <td key={index}>{inline(row[index] ?? "")}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function block(lines: string[], key: number): React.JSX.Element {
  const kind = lineKind(lines[0] ?? "");
  if (kind === "table") {
    const rendered = table(lines, key);
    if (rendered !== null) return rendered;
  }
  if (kind === "bullet") return <ul key={key}>{lines.map((line, index) => <li key={index}>{inline(line.replace(BULLET, ""))}</li>)}</ul>;
  if (kind === "numbered") return <ol key={key}>{lines.map((line, index) => <li key={index}>{inline(line.replace(NUMBERED, ""))}</li>)}</ol>;
  if (kind === "single" && RULE.test(lines[0] ?? "")) return <hr key={key} />;
  if (kind === "single") return <p key={key}><strong>{inline((lines[0] ?? "").replace(HEADING, "").replaceAll("**", ""))}</strong></p>;
  return <p key={key}>{inline(lines.join("\n"))}</p>;
}

/** Groups lines into blocks at blank lines and wherever the kind of line changes. */
function blocks(body: string): string[][] {
  const grouped: string[][] = [];
  let current: string[] = [];
  let currentKind: LineKind | null = null;
  for (const line of body.split("\n")) {
    const kind = line.trim() === "" ? null : lineKind(line);
    if (kind === null || kind !== currentKind || kind === "single") {
      if (current.length > 0) grouped.push(current);
      current = [];
    }
    currentKind = kind;
    if (kind !== null) current.push(line);
  }
  if (current.length > 0) grouped.push(current);
  return grouped;
}

/**
 * Renders the Markdown subset local models use in chat answers (paragraphs, bold, italics,
 * inline code, bullet and numbered lists, headings, rules, and tables) as React elements.
 * Never injects HTML.
 */
export function ChatAnswerText({ body }: { body: string }): React.JSX.Element {
  return <div className="chat-message__body chat-message__body--formatted">{blocks(body).map((lines, index) => block(lines, index))}</div>;
}
