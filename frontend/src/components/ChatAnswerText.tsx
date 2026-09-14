import type React from "react";

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*#{1,6}\s+/;

function inline(text: string): React.ReactNode[] {
  return text
    .split(/(\*\*[^*\n]+?\*\*)/g)
    .filter((part) => part !== "")
    .map((part, index) => (part.length > 4 && part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part));
}

function block(lines: string[], key: number): React.JSX.Element {
  if (lines.every((line) => BULLET.test(line))) {
    return <ul key={key}>{lines.map((line, index) => <li key={index}>{inline(line.replace(BULLET, ""))}</li>)}</ul>;
  }
  if (lines.every((line) => NUMBERED.test(line))) {
    return <ol key={key}>{lines.map((line, index) => <li key={index}>{inline(line.replace(NUMBERED, ""))}</li>)}</ol>;
  }
  if (lines.length === 1 && HEADING.test(lines[0] ?? "")) {
    return <p key={key}><strong>{inline((lines[0] ?? "").replace(HEADING, "").replaceAll("**", ""))}</strong></p>;
  }
  return <p key={key}>{inline(lines.join("\n"))}</p>;
}

/**
 * Renders the small Markdown subset local models use in chat answers (paragraphs, bold,
 * bullet and numbered lists, headings) as React elements. Never injects HTML.
 */
export function ChatAnswerText({ body }: { body: string }): React.JSX.Element {
  const blocks = body.split(/\n\s*\n/).map((chunk) => chunk.split("\n").filter((line) => line.trim() !== "")).filter((lines) => lines.length > 0);
  return <div className="chat-message__body chat-message__body--formatted">{blocks.map((lines, index) => block(lines, index))}</div>;
}
