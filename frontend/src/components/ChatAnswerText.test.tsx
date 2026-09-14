import { render, screen } from "@testing-library/react";

import { ChatAnswerText } from "./ChatAnswerText";

describe("ChatAnswerText", () => {
  it("renders bold, bullet lists, and headings from a synthetic answer", () => {
    const { container } = render(<ChatAnswerText body={"### Summary\n\nYou averaged **7234** steps.\n\n- 26 days with data\n- 4 days missing"} />);
    expect(screen.getByText("Summary").tagName).toBe("STRONG");
    expect(screen.getByText("7234").tagName).toBe("STRONG");
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual(["26 days with data", "4 days missing"]);
    expect(container.textContent).not.toContain("**");
  });

  it("keeps markup-looking text as inert text", () => {
    const { container } = render(<ChatAnswerText body={"<img src=x onerror=alert(1)> **<b>bold</b>**"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("<b>bold</b>").tagName).toBe("STRONG");
  });
});
