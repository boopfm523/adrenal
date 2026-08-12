import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { acknowledgeGarminSyncFinding, getDataQuality, type DataQuality } from "../api/client";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";

type Finding = DataQuality["findings"][number];

function FindingsTable({ findings, absence = false, onAcknowledge, pendingId }: { findings: Finding[]; absence?: boolean; onAcknowledge?: (finding: Finding) => void; pendingId?: string | undefined }): React.JSX.Element {
  const label = absence ? "Known genuine absences" : "Data-quality problems";
  return <div className="table-scroll" tabIndex={0} role="region" aria-label={`${label} table`}><table><caption>{label}, deterministically generated from the current owner-scoped record state.</caption><thead><tr><th scope="col">Finding</th><th scope="col">Source and severity</th><th scope="col">Details</th><th scope="col">Record reference</th><th scope="col">Action</th></tr></thead><tbody>{findings.map((finding) => <tr key={finding.id}><th scope="row">{finding.title}</th><td><span>{finding.source}</span><span>{absence ? "genuine absence" : finding.severity}</span></td><td>{finding.detail}</td><td>{finding.record_id === null ? "Not applicable" : <code>{finding.record_id}</code>}</td><td><Link className="button-link" to={finding.href}>{finding.action_label}</Link>{finding.can_acknowledge && onAcknowledge !== undefined ? <button type="button" className="button-secondary" disabled={pendingId === finding.id} onClick={() => { onAcknowledge(finding); }}>{pendingId === finding.id ? "Clearing…" : "Clear reviewed notice"}</button> : null}</td></tr>)}</tbody></table></div>;
}

export function DataQualityPage(): React.JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [clearedIds, setClearedIds] = useState<Set<string>>(new Set());
  const rawPage = searchParams.get("page") ?? "";
  const page = /^\d+$/.test(rawPage) && Number(rawPage) >= 1 ? Number(rawPage) : 1;
  const query = useQuery({ queryKey: ["data-quality", page], queryFn: () => getDataQuality(page) });
  const acknowledge = useMutation({
    mutationFn: ({ id }: { id: string }) => acknowledgeGarminSyncFinding(id),
    onSuccess: async (_result, variables) => {
      setClearedIds((current) => new Set(current).add(`garmin-sync:${variables.id}`));
      await queryClient.invalidateQueries({ queryKey: ["data-quality"] });
    },
  });

  if (query.isPending) return <Page title="Data quality" description="Review drafts, import failures, integration gaps, and operational failures."><p role="status">Checking data quality…</p></Page>;
  if (query.isError) return <Page title="Data quality" description="Review drafts, import failures, integration gaps, and operational failures."><p className="error-summary" role="alert">Data-quality findings could not be loaded. No conclusion about record completeness can be made.</p></Page>;

  const visibleFindings = query.data.findings.filter((finding) => !clearedIds.has(finding.id));
  const problems = visibleFindings.filter((finding) => finding.finding_kind === "problem");
  const absences = visibleFindings.filter((finding) => finding.finding_kind === "genuine_absence");

  return (
    <Page title="Data quality" description="Review drafts, import failures, integration gaps, and operational failures.">
      <aside className="safety-note"><strong>Completeness boundary:</strong> {query.data.completeness_notice}</aside>
      <p className="privacy-note">This is a current derived review queue, not a dated event history. Open stress episodes appear here after 24 hours so you can confirm that they are continuing or record their actual end; HealthCurve never invents an end time. A Garmin warning notice describes a completed sync unless it explicitly says otherwise; it is not another queued run. Clear reviewed notice hides that notice without deleting sync history or health data. A later sync with new warnings can create a new notice.</p>
      {visibleFindings.length === 0 ? <section className="empty-state"><h2>No known data-quality findings</h2><p>The checks found no current items to review. This does not establish that the health record is complete.</p></section> : null}
      <section aria-labelledby="problems-heading">
        <span id="drafts" className="anchor-target" aria-hidden="true" />
        <span id="operations" className="anchor-target" aria-hidden="true" />
        <h2 id="problems-heading">Problems to review</h2>
        <p>These records or operations need review. Follow the action on each finding; HealthCurve does not silently repair recorded facts.</p>
        {problems.length === 0 ? <p>No known operational or record problems.</p> : <FindingsTable findings={problems} pendingId={acknowledge.isPending ? acknowledge.variables.id : undefined} onAcknowledge={(finding) => { if (finding.record_id !== null) acknowledge.mutate({ id: finding.record_id }); }} />}
        {acknowledge.isError ? <p className="error-summary" role="alert">The reviewed notice could not be cleared. No sync history or health data was changed.</p> : null}
      </section>
      <section id="source-absences" aria-labelledby="absences-heading" className="quality-section">
        <h2 id="absences-heading">Known genuine absences</h2>
        <p>These metrics were not supplied by the source. Missing means unavailable: it is not a recorded value of zero and is not automatically an error.</p>
        {absences.length === 0 ? <p>No known provider-reported metric absences.</p> : <FindingsTable findings={absences} absence />}
      </section>
      <PaginationControls label="Data-quality findings" metadata={query.data.page} onPageChange={(nextPage) => { const params = new URLSearchParams(searchParams); if (nextPage === 1) params.delete("page"); else params.set("page", nextPage.toString()); setSearchParams(params); }} />
    </Page>
  );
}
