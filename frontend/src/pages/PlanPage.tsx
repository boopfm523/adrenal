import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getActiveRegimen, getRegimenDiff, getRegimens, type RegimenVersion } from "../api/client";
import { PlanCard } from "../components/CategoryCards";
import { Page } from "../components/Page";

function ApprovalProvenance({ version }: { version: RegimenVersion }): React.JSX.Element {
  if (version.status !== "approved") return <p className="draft-warning">Draft plan—not physician approved. This version is not in force.</p>;
  return <dl className="provenance-grid">
    <div><dt>Approved by</dt><dd>{version.approved_by ?? "Provenance missing"}</dd></div>
    <div><dt>Approval source</dt><dd>{version.approval_source ?? "Provenance missing"}</dd></div>
    <div><dt>Approved at</dt><dd>{version.approved_at ?? "Provenance missing"}</dd></div>
    <div><dt>Effective dates</dt><dd>{version.effective_from} through {version.effective_to ?? "ongoing"}</dd></div>
  </dl>;
}

function PlanContents({ version }: { version: RegimenVersion }): React.JSX.Element {
  const slots = version.slots ?? [];
  const instructions = version.instructions ?? [];
  return <>
    <ApprovalProvenance version={version} />
    <h3>Scheduled slots</h3>
    {slots.length === 0 ? <p>No scheduled slots recorded.</p> : <ul className="plan-list">{slots.map((slot) => <li key={slot.id}><strong>{slot.scheduled_local_time.slice(0, 5)}</strong> · {slot.medication_name} · {slot.amount} {slot.unit} · {slot.route}{slot.condition === null ? null : <span> · {slot.condition}</span>}</li>)}</ul>}
    <h3>Physician-authored instructions</h3>
    {instructions.length === 0 ? <p>No instructions recorded in this version.</p> : instructions.map((instruction) => <article className="instruction-card" key={instruction.id}><h4>{instruction.title}</h4><p>{instruction.body}</p><p>Authored by {instruction.authored_by} on {instruction.authored_on}</p></article>)}
  </>;
}

export function PlanPage(): React.JSX.Element {
  const active = useQuery({ queryKey: ["regimens", "active"], queryFn: getActiveRegimen });
  const history = useQuery({ queryKey: ["regimens", "history"], queryFn: getRegimens });
  const [olderId, setOlderId] = useState("");
  const [newerId, setNewerId] = useState("");
  const chronological = [...(history.data ?? [])].sort((a, b) => a.effective_from.localeCompare(b.effective_from));
  const selectedOlderId = olderId !== "" ? olderId : (chronological[chronological.length - 2]?.id ?? "");
  const selectedNewerId = newerId !== "" ? newerId : (chronological[chronological.length - 1]?.id ?? "");
  const diff = useQuery({ queryKey: ["regimen-diff", selectedOlderId, selectedNewerId], queryFn: () => getRegimenDiff(selectedOlderId, selectedNewerId), enabled: selectedOlderId !== "" && selectedNewerId !== "" && selectedOlderId !== selectedNewerId });

  return <Page title="Medication plan" description="Read-only physician-approved schedules and their provenance, kept separate from actual recorded doses.">
    {(active.isPending || history.isPending) ? <p role="status">Loading medication plan…</p> : null}
    {(active.isError || history.isError) ? <p className="error-summary" role="alert">Medication plan history could not be loaded.</p> : null}
    {active.data === null ? <section className="empty-state"><h2>No approved plan currently in force</h2><p>Draft and historical versions appear below, but HealthCurve will not treat them as the active plan.</p></section> : null}
    {active.data === undefined || active.data === null ? null : <PlanCard title={`${active.data.version_label} · currently in force`}><PlanContents version={active.data} /></PlanCard>}

    <section aria-labelledby="history-heading"><h2 id="history-heading">Version history</h2>
      {history.data?.length === 0 ? <p>No plan versions recorded.</p> : null}
      <div className="version-history">{history.data?.map((version) => <article className={`version-card version-card--${version.status}`} key={version.id}><p className="category-label">{version.status === "draft" ? "Draft plan—not physician approved" : version.status === "approved" ? "Physician-approved plan" : "Retired plan version"}</p><h3>{version.version_label}</h3><ApprovalProvenance version={version} /><details><summary>Show slots and instructions</summary><PlanContents version={version} /></details></article>)}</div>
    </section>

    <section aria-labelledby="diff-heading"><h2 id="diff-heading">Compare versions</h2>
      {history.data !== undefined && history.data.length < 2 ? <p>At least two versions are needed for a comparison.</p> : <div className="diff-controls"><label>Older version<select value={selectedOlderId} onChange={(event) => { setOlderId(event.target.value); }}>{history.data?.map((version) => <option key={version.id} value={version.id}>{version.version_label}</option>)}</select></label><label>Newer version<select value={selectedNewerId} onChange={(event) => { setNewerId(event.target.value); }}>{history.data?.map((version) => <option key={version.id} value={version.id}>{version.version_label}</option>)}</select></label></div>}
      {selectedOlderId === selectedNewerId && selectedOlderId !== "" ? <p className="error-summary" role="alert">Choose two different versions.</p> : null}
      {diff.isPending && diff.isFetching ? <p role="status">Calculating deterministic version diff…</p> : null}
      {diff.isError ? <p className="error-summary" role="alert">The version comparison could not be loaded.</p> : null}
      {diff.data === undefined ? null : <div className="version-diff">{(["added", "removed", "changed"] as const).map((kind) => <section key={kind}><h3>{kind[0]?.toUpperCase()}{kind.slice(1)}</h3>{diff.data[kind]?.length === 0 ? <p>No {kind} schedule entries.</p> : <ul>{diff.data[kind]?.map((entry) => <li key={entry}>{entry}</li>)}</ul>}</section>)}</div>}
    </section>
  </Page>;
}
