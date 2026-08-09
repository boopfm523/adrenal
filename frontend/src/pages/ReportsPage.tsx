import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createReport, getReport, getReports, type ReportCreate, type ReportPreview } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { localDate } from "../time";

const sectionOptions = [
  ["metrics", "Deterministic metrics"],
  ["doses", "Recorded doses"],
  ["approved_plan", "Physician-approved plan"],
  ["episodes", "Stress/up-dose episodes"],
  ["symptoms", "Symptoms"],
  ["emergency_injections", "Emergency injections"],
  ["patient_notes", "Patient notes and questions"],
  ["life_events", "Life events"],
  ["labs", "Laboratory results"],
  ["wearables", "Garmin wearables"],
] as const;

type ReportSection = (typeof sectionOptions)[number][0];
const defaultSections: ReportSection[] = sectionOptions.map(([value]) => value).filter((value) => value !== "life_events");

function records(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function jsonText(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function reportDateError(dateFrom: string, dateTo: string): string | null {
  const start = Date.parse(`${dateFrom}T00:00:00Z`);
  const end = Date.parse(`${dateTo}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "Enter both report dates.";
  if (end < start) return "Through date must be on or after the from date.";
  if ((end - start) / (24 * 60 * 60 * 1000) + 1 > 366) return "Report range cannot exceed 366 days.";
  return null;
}

function CategoryPreview({ preview, category, title, className, warning }: { preview: ReportPreview; category: "fact" | "plan" | "patient_note" | "ai"; title: string; className: string; warning?: string }): React.JSX.Element | null {
  const categoryRecords = records(preview.snapshot_content[category]);
  if (category === "ai" && !preview.include_ai) return null;
  return <section className={`category-card ${className}`} aria-labelledby={`report-${category}-heading`}><p className="category-label">{title}</p><h3 id={`report-${category}-heading`}>{title}</h3>{warning === undefined ? null : <p className="category-disclaimer">{warning}</p>}{categoryRecords.length === 0 ? <p>No selected records in this category.</p> : categoryRecords.map((record, index) => <pre className="report-record" key={`${category}-${index.toString()}`}>{jsonText(record)}</pre>)}</section>;
}

function ReportPreviewPanel({ preview }: { preview: ReportPreview }): React.JSX.Element {
  return <section aria-labelledby="report-preview-heading"><h2 id="report-preview-heading">Snapshot preview</h2><p><strong>{preview.date_from}</strong> through <strong>{preview.date_to}</strong> in <strong>{preview.timezone}</strong>. Snapshot <code>{preview.canonical_sha256}</code>.</p><aside className="safety-note">This is frozen report content. Later corrections to source records do not silently rewrite this snapshot.</aside><section className="metric-card"><h3>Deterministic metrics</h3>{Object.keys(preview.metric_values).length === 0 ? <p>No metrics selected.</p> : Object.entries(preview.metric_values).map(([name, metric]) => <details className="metric-definition" key={name}><summary>{name.replaceAll("_", " ")}</summary><pre className="report-record">{jsonText(metric)}</pre></details>)}</section><CategoryPreview preview={preview} category="fact" title="Recorded facts" className="category-card--fact" /><CategoryPreview preview={preview} category="plan" title="Physician-approved plan" className="category-card--plan" warning="Approved plan content is separate from recorded actual doses." /><CategoryPreview preview={preview} category="patient_note" title="Patient notes and questions" className="category-card--patient" /><CategoryPreview preview={preview} category="ai" title="AI-generated analysis" className="category-card--ai" warning="Generated content—not a recorded fact or physician-approved instruction. Review against its cited sources." /></section>;
}

export function ReportsPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const [form, setForm] = useState(() => { const now = new Date(); return { dateFrom: localDate(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000), timezone), dateTo: localDate(now, timezone), timezone, sections: defaultSections, includeAi: false, includeSensitive: false, csv: false, json: false }; });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: ["reports"], queryFn: getReports });
  const preview = useQuery({ queryKey: ["report", selectedId], queryFn: () => getReport(selectedId ?? ""), enabled: selectedId !== null });
  const generation = useMutation({ mutationFn: createReport, onSuccess: async (created) => { setSelectedId(created.id); await queryClient.invalidateQueries({ queryKey: ["reports"] }); } });
  const dateError = reportDateError(form.dateFrom, form.dateTo);

  function toggleSection(value: ReportSection, checked: boolean): void { setForm({ ...form, sections: checked ? [...form.sections, value] : form.sections.filter((section) => section !== value) }); }
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void { event.preventDefault(); const payload: ReportCreate = { date_from: form.dateFrom, date_to: form.dateTo, timezone: form.timezone, selected_sections: form.sections, include_ai: form.includeAi, include_sensitive: form.includeSensitive, companion_formats: [...(form.csv ? ["csv" as const] : []), ...(form.json ? ["json" as const] : [])] }; generation.mutate(payload); }

  return <Page title="Reports" description="Build immutable, physician-ready snapshots from recorded facts and approved plan data.">
    <aside className="safety-note"><strong>Report boundary:</strong> A report describes selected records and deterministic metrics. It is not medical advice and does not establish causation. AI is excluded unless you opt in below.</aside>
    <div className="report-layout"><section aria-labelledby="builder-heading"><h2 id="builder-heading">Build a report</h2><form className="report-builder" onSubmit={submit}><div className="report-range"><label>From date<input required type="date" value={form.dateFrom} onChange={(event) => { setForm({ ...form, dateFrom: event.target.value }); }} /></label><label>Through date<input required type="date" value={form.dateTo} onChange={(event) => { setForm({ ...form, dateTo: event.target.value }); }} /></label><label>IANA timezone<input required value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} /></label></div>{dateError === null ? null : <p className="error-summary" role="alert">{dateError}</p>}<fieldset><legend>Sections</legend><div className="report-options">{sectionOptions.map(([value, label]) => <label className="checkbox-label" key={value}><input type="checkbox" checked={form.sections.includes(value)} onChange={(event) => { toggleSection(value, event.target.checked); }} />{label}</label>)}</div></fieldset><fieldset><legend>Privacy and companions</legend><div className="report-options"><label className="checkbox-label"><input type="checkbox" checked={form.includeAi} onChange={(event) => { setForm({ ...form, includeAi: event.target.checked }); }} />Include separately labeled AI-generated analysis</label>{form.includeAi ? <p className="draft-warning">AI content is generated, may be wrong, and will be boxed separately with source provenance.</p> : null}<label className="checkbox-label"><input type="checkbox" checked={form.includeSensitive} onChange={(event) => { setForm({ ...form, includeSensitive: event.target.checked }); }} />Include notes marked sensitive</label><label className="checkbox-label"><input type="checkbox" checked={form.csv} onChange={(event) => { setForm({ ...form, csv: event.target.checked }); }} />Include CSV companion</label><label className="checkbox-label"><input type="checkbox" checked={form.json} onChange={(event) => { setForm({ ...form, json: event.target.checked }); }} />Include JSON companion</label></div></fieldset><button type="submit" disabled={generation.isPending || form.sections.length === 0 || dateError !== null}>{generation.isPending ? "Generating locally…" : "Generate immutable report"}</button>{form.sections.length === 0 ? <p className="error-summary" role="alert">Select at least one section.</p> : null}{generation.isError ? <p className="error-summary" role="alert">The report could not be generated. Check the date range, timezone, and selected sections.</p> : null}</form></section>
    <section aria-labelledby="history-heading"><h2 id="history-heading">Snapshot history</h2>{history.isPending ? <p role="status">Loading report history…</p> : null}{history.isError ? <p className="error-summary" role="alert">Report history could not be loaded.</p> : null}{history.data?.length === 0 ? <div className="empty-state"><h3>No report snapshots yet</h3><p>Choose a range and sections to create the first immutable report.</p></div> : null}<div className="report-history">{history.data?.map((report) => <article className="version-card" key={report.id}><h3>{report.date_from} through {report.date_to}</h3><p>{report.timezone} · created <time dateTime={report.created_at}>{new Date(report.created_at).toLocaleString()}</time></p><p>{report.include_ai ? "Includes separately labeled AI" : "AI excluded"}</p><div className="quick-actions"><button type="button" className="button-secondary" onClick={() => { setSelectedId(report.id); }}>Preview snapshot</button>{report.artifacts.map((artifact) => <a className="button-link" href={artifact.download_url} key={artifact.format}>Download {artifact.format.toUpperCase()}</a>)}</div></article>)}</div></section></div>
    {preview.isPending && selectedId !== null ? <p role="status">Loading immutable snapshot…</p> : null}{preview.isError ? <p className="error-summary" role="alert">The selected snapshot could not be loaded or failed its integrity check.</p> : null}{preview.data === undefined ? null : <ReportPreviewPanel preview={preview.data} />}
  </Page>;
}
