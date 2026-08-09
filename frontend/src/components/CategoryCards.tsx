import type { PropsWithChildren, ReactNode } from "react";

interface CategoryCardProps extends PropsWithChildren {
  title: string;
  metadata?: ReactNode;
}

interface ContextCardProps extends CategoryCardProps {
  headingLevel?: 2 | 4;
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

export function ContextCard({ title, metadata, children, headingLevel = 2 }: ContextCardProps): React.JSX.Element {
  const Heading = headingLevel === 4 ? "h4" : "h2";
  return (
    <article className="category-card category-card--context" data-category="context">
      <p className="category-label"><span aria-hidden="true">◉</span> Environmental context</p>
      <Heading>{title}</Heading>
      {children}
      <p className="category-disclaimer">Contextual observation—not a symptom, dose, physician instruction, or AI conclusion.</p>
      {metadata === undefined ? null : <footer>{metadata}</footer>}
    </article>
  );
}
