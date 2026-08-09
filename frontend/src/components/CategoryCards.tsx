import type { PropsWithChildren, ReactNode } from "react";

interface CategoryCardProps extends PropsWithChildren {
  title: string;
  metadata?: ReactNode;
}

export function FactCard({ title, metadata, children }: CategoryCardProps): React.JSX.Element {
  return (
    <article className="category-card category-card--fact" data-category="fact">
      <p className="category-label"><span aria-hidden="true">●</span> Recorded fact</p>
      <h2>{title}</h2>
      {children}
      {metadata === undefined ? null : <footer>{metadata}</footer>}
    </article>
  );
}

export function PlanCard({ title, metadata, children }: CategoryCardProps): React.JSX.Element {
  return (
    <section className="category-card category-card--plan" data-category="plan">
      <p className="category-label"><span aria-hidden="true">▣</span> Physician-approved plan</p>
      <h2>{title}</h2>
      {children}
      {metadata === undefined ? null : <footer>{metadata}</footer>}
    </section>
  );
}

export function AiAnalysisCard({ title, metadata, children }: CategoryCardProps): React.JSX.Element {
  return (
    <aside className="category-card category-card--ai" data-category="ai">
      <p className="category-label"><span aria-hidden="true">◇</span> AI-generated observation</p>
      <h2>{title}</h2>
      {children}
      <p className="category-disclaimer">Generated analysis—not medical advice or a physician-approved plan.</p>
      {metadata === undefined ? null : <footer>{metadata}</footer>}
    </aside>
  );
}
