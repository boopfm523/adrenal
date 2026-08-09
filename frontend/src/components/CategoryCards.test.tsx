import { render, screen, within } from "@testing-library/react";

import { AiAnalysisCard, ContextCard, FactCard, PlanCard } from "./CategoryCards";

describe("category components", () => {
  it("distinguishes facts, approved plans, and AI without relying on colour", () => {
    const { container } = render(
      <>
        <FactCard title="Recorded item">Synthetic fact content</FactCard>
        <PlanCard title="Approved item">Synthetic plan content</PlanCard>
        <AiAnalysisCard title="Generated item">Synthetic analysis content</AiAnalysisCard>
        <ContextCard title="Context item">Synthetic context content</ContextCard>
      </>,
    );

    const fact = container.querySelector("[data-category='fact']");
    const plan = container.querySelector("[data-category='plan']");
    const ai = container.querySelector("[data-category='ai']");
    const context = container.querySelector("[data-category='context']");
    expect(within(fact as HTMLElement).getByText("Recorded fact", { exact: false })).toBeVisible();
    expect(within(plan as HTMLElement).getByText("Physician-approved plan", { exact: false })).toBeVisible();
    expect(within(ai as HTMLElement).getByText("AI-generated observation", { exact: false })).toBeVisible();
    expect(within(context as HTMLElement).getByText("Environmental context", { exact: false })).toBeVisible();
    expect(screen.getByText(/Generated analysis—not medical advice/)).toBeVisible();
    expect(fact).toHaveProperty("tagName", "ARTICLE");
    expect(plan).toHaveProperty("tagName", "SECTION");
    expect(ai).toHaveProperty("tagName", "ASIDE");
    expect(context).toHaveProperty("tagName", "ARTICLE");
  });

  it("renders untrusted child text as text rather than markup", () => {
    render(<AiAnalysisCard title="Draft">{"<img src=x onerror=alert(1)>"}</AiAnalysisCard>);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(document.querySelector("img")).not.toBeInTheDocument();
  });
});
