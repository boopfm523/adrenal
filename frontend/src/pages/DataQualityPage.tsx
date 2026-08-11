import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { getDataQuality, type DataQuality } from "../api/client";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";

type Finding = DataQuality["findings"][number];

function FindingCard({ finding, absence = false }: { finding: Finding; absence?: boolean }): React.JSX.Element {
  return (
    <article className={`quality-card${absence ? " quality-card--absence" : " quality-card--problem"}`}>
      <p className="quality-card__label">{absence ? "Known genuine absence" : `${finding.severity} problem`} · {finding.source}</p>
      <h3>{finding.title}</h3>
      <p>{finding.detail}</p>
      {finding.record_id === null ? null : <p className="quality-card__reference">Record reference: <code>{finding.record_id}</code></p>}
      <Link className="button-link" to={finding.href}>{finding.action_label}</Link>
    </article>
  );
}

export function DataQualityPage(): React.JSX.Element {
  const [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ["data-quality", page], queryFn: () => getDataQuality(page) });

  if (query.isPending) return <Page title="Data quality" description="Review drafts, import failures, integration gaps, and operational failures."><p role="status">Checking data quality…</p></Page>;
  if (query.isError) return <Page title="Data quality" description="Review drafts, import failures, integration gaps, and operational failures."><p className="error-summary" role="alert">Data-quality findings could not be loaded. No conclusion about record completeness can be made.</p></Page>;

  const problems = query.data.findings.filter((finding) => finding.finding_kind === "problem");
  const absences = query.data.findings.filter((finding) => finding.finding_kind === "genuine_absence");

  return (
    <Page title="Data quality" description="Review drafts, import failures, integration gaps, and operational failures.">
      <aside className="safety-note"><strong>Completeness boundary:</strong> {query.data.completeness_notice}</aside>
      {query.data.findings.length === 0 ? <section className="empty-state"><h2>No known data-quality findings</h2><p>The checks found no current items to review. This does not establish that the health record is complete.</p></section> : null}
      <section aria-labelledby="problems-heading">
        <span id="drafts" className="anchor-target" aria-hidden="true" />
        <span id="operations" className="anchor-target" aria-hidden="true" />
        <h2 id="problems-heading">Problems to review</h2>
        <p>These records or operations need review. Follow the action on each finding; HealthCurve does not silently repair recorded facts.</p>
        {problems.length === 0 ? <p>No known operational or record problems.</p> : <div className="quality-grid">{problems.map((finding) => <FindingCard key={finding.id} finding={finding} />)}</div>}
      </section>
      <section id="source-absences" aria-labelledby="absences-heading" className="quality-section">
        <h2 id="absences-heading">Known genuine absences</h2>
        <p>These metrics were not supplied by the source. Missing means unavailable: it is not a recorded value of zero and is not automatically an error.</p>
        {absences.length === 0 ? <p>No known provider-reported metric absences.</p> : <div className="quality-grid">{absences.map((finding) => <FindingCard key={finding.id} finding={finding} absence />)}</div>}
      </section>
      <PaginationControls label="Data-quality findings" metadata={query.data.page} onPageChange={setPage} />
    </Page>
  );
}
