import { render, screen, within } from "@testing-library/react";

import { ChatAnswerText } from "./ChatAnswerText";

describe("ChatAnswerText", () => {
  it("renders bold, bullet lists, and headings from a synthetic answer", () => {
    const { container } = render(<ChatAnswerText body={"### Summary\n\nYou averaged **7234** steps.\n\n- 26 days with data\n- 4 days missing"} />);
    expect(screen.getByText("Summary").tagName).toBe("STRONG");
    expect(screen.getByText("7234").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual(["26 days with data", "4 days missing"]);
    expect(container.textContent).not.toContain("**");
  });

  it("renders a Markdown table as a semantic table, even right after a sentence", () => {
    const body = "Past week, 7 synthetic nights:\n| Metric | Average | Latest |\n|---|---|---|\n| **Bedtime** | 23:10 | 00:53 |\n| Wake time | 06:00 |";
    const { container } = render(<ChatAnswerText body={body} />);
    expect(screen.getByText("Past week, 7 synthetic nights:").tagName).toBe("P");
    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Metric", "Average", "Latest"]);
    const rows = screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));
    expect(rows).toEqual([["Bedtime", "23:10", "00:53"], ["Wake time", "06:00", ""]]);
    expect(screen.getByText("Bedtime").tagName).toBe("STRONG");
    expect(container.textContent).not.toContain("|");
    expect(container.textContent).not.toContain("---");
  });

  it("renders inline code, italics, rules, and a list that directly follows a sentence", () => {
    const { container } = render(<ChatAnswerText body={"Details:\n- uses `analytics.doses`\n- *approximate*\n\n---\n\nDone."} />);
    expect(screen.getByText("Details:").tagName).toBe("P");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("analytics.doses").tagName).toBe("CODE");
    expect(screen.getByText("approximate").tagName).toBe("EM");
    expect(container.querySelector("hr")).not.toBeNull();
    expect(screen.getByText("Done.").tagName).toBe("P");
  });

  it("keeps markup-looking text as inert text", () => {
    const { container } = render(<ChatAnswerText body={"<img src=x onerror=alert(1)> **<b>bold</b>**"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("<b>bold</b>").tagName).toBe("STRONG");
  });
});
