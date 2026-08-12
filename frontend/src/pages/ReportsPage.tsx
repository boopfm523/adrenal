import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Fieldset, Group, Paper, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { createReport, getReport, getReports, type RecordedHistoryFilters, type ReportCreate, type ReportPreview } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { localDate, timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "../time";

const sectionOptions = [
  ["metrics", "Deterministic metrics"],
  ["doses", "Recorded doses"],
  ["approved_plan", "Physician-approved plan"],
  ["episodes", "Stress/up-dose episodes"],
  ["symptoms", "Symptoms"],
  ["vitals", "Blood pressure, weight, and temperature"],
  ["emergency_injections", "Emergency injections"],
  ["patient_notes", "Patient notes and questions"],
  ["life_events", "Life events"],
  ["labs", "Laboratory results"],
  ["wearables", "Garmin wearables"],
] as const;

type ReportSection = (typeof sectionOptions)[number][0];
const defaultSections: ReportSection[] = sectionOptions.map(([value]) => value).filter((value) => value !== "life_events");

interface ReportHistoryView extends RecordedHistoryFilters { page: number }
function reportHistoryView(search: string, profileTimezone: string): ReportHistoryView { const params = new URLSearchParams(search); const rawPage = params.get("history_page") ?? ""; return { dateFrom: params.get("history_date_from") ?? "", dateTo: params.get("history_date_to") ?? "", timezone: params.get("history_timezone") ?? profileTimezone, page: /^\d+$/.test(rawPage) && Number(rawPage) >= 1 ? Number(rawPage) : 1 }; }
function reportHistorySearch(view: ReportHistoryView): URLSearchParams { const params = new URLSearchParams(); if (view.dateFrom !== "") params.set("history_date_from", view.dateFrom); if (view.dateTo !== "") params.set("history_date_to", view.dateTo); params.set("history_timezone", view.timezone); if (view.page > 1) params.set("history_page", view.page.toString()); return params; }

function records(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function object(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "not recorded";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.map(display).join(", ");
  if (typeof value !== "string" && typeof value !== "number" && typeof value !== "bigint") return "recorded value";
  const text = typeof value === "string" ? value.replaceAll("_", " ") : value.toString();
  return /^-?\d+(?:\.\d+)?$/.test(text) ? text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") : text;
}

function recordSummary(value: unknown): { title: string; details: string[] } {
  const record = object(value);
  const type = display(record.record_type ?? "recorded fact");
  const when = display(record.local_time ?? record.started_at ?? record.effective_from ?? record.generated_at);
  if (record.record_type === "dose") return { title: `${display(record.medication_name)} - ${display(record.amount)} ${display(record.unit)}`, details: [when, `${display(record.category)}; ${display(record.route)}`] };
  if (record.record_type === "symptom") return { title: display(record.name), details: [when, record.severity === null || record.severity === undefined ? "severity not recorded" : `severity ${display(record.severity)}/10`, record.body_area === null || record.body_area === undefined ? "" : display(record.body_area)] };
  if (record.record_type === "stress_episode") return { title: display(record.trigger), details: [when, `${display(record.severity)}; ${display(record.status)}`] };
  if (record.record_type === "blood_pressure") return { title: `Blood pressure ${display(record.systolic_mmhg)}/${display(record.diastolic_mmhg)} mmHg`, details: [when, display(record.measurement_setting), record.pulse_bpm === null || record.pulse_bpm === undefined ? "" : `pulse ${display(record.pulse_bpm)} bpm`] };
  if (record.record_type === "weight") return { title: `Weight ${display(record.display_lb)} lb`, details: [when, display(record.measurement_setting)] };
  if (record.record_type === "temperature") return { title: `Temperature ${String(record.display_f)} °F (${String(record.display_c)} °C)`, details: [when] };
  if (record.record_type === "garmin_metric") return { title: `${display(record.metric_type)}: ${display(record.value)} ${display(record.unit)}`, details: [when, "Garmin observation"] };
  if (record.record_type === "garmin_metric_summary") return { title: `${display(record.metric_type)} — ${display(record.local_date)}`, details: [`average ${display(record.average)}; low ${display(record.low)}; high ${display(record.high)} ${display(record.unit)}`, `${display(record.sample_count)} samples`] };
  if (record.record_type === "approved_regimen") return { title: display(record.version_label), details: [`Effective ${when}`, `${records(record.slots).length.toString()} scheduled slot(s)`] };
  if (record.record_type === "patient_note") return { title: display(record.text), details: [when] };
  if (record.record_type === "ai_analysis") return { title: display(record.analysis_type), details: [when, display(record.body)] };
  const primary = record.title ?? record.name ?? record.text ?? type;
  return { title: display(primary), details: [when, type] };
}

function previewRecords(value: unknown, category: "fact" | "plan" | "patient_note" | "ai"): unknown[] {
  const categoryRecords = records(value);
  if (category !== "fact") return categoryRecords;
  const ordinary = categoryRecords.filter((value) => object(value).record_type !== "garmin_metric");
  const grouped = new Map<string, { local_date: string; metric_type: string; unit: string; values: number[] }>();
  for (const value of categoryRecords) {
    const record = object(value);
    if (record.record_type !== "garmin_metric") continue;
    const number = Number(record.value);
    if (!Number.isFinite(number)) continue;
    const localDate = display(record.local_time).slice(0, 10);
    const metricType = display(record.metric_type);
    const unit = display(record.unit);
    const key = `${localDate}|${metricType}|${unit}`;
    const current = grouped.get(key) ?? { local_date: localDate, metric_type: metricType, unit, values: [] };
    current.values.push(number);
    grouped.set(key, current);
  }
  const summaries = [...grouped.values()].sort((left, right) => `${left.local_date}|${left.metric_type}`.localeCompare(`${right.local_date}|${right.metric_type}`)).map((group) => ({
    record_type: "garmin_metric_summary",
    local_date: group.local_date,
    metric_type: group.metric_type,
    unit: group.unit,
    average: (group.values.reduce((sum, value) => sum + value, 0) / group.values.length).toFixed(1),
    low: Math.min(...group.values),
    high: Math.max(...group.values),
    sample_count: group.values.length,
  }));
  return [...ordinary, ...summaries];
}

function MetricPreview({ name, value }: { name: string; value: unknown }): React.JSX.Element {
  const metric = object(value);
  const lines: string[] = [];
  if (name === "daily_doses") {
    for (const rowValue of records(metric.values)) { const row = object(rowValue); lines.push(`${display(row.date)}: recorded ${display(row.actual_total)} ${display(row.unit)} in ${display(row.recorded_dose_count)} dose(s); ${row.planned_total === null || row.planned_total === undefined ? "no approved plan" : `planned ${display(row.planned_total)} ${display(row.unit)}`}`); }
  } else if (name === "symptoms") {
    lines.push(`${display(metric.count)} symptom(s); ${display(metric.missing_count)} missing severity`);
    const frequency = object(metric.frequency); if (Object.keys(frequency).length > 0) lines.push(Object.entries(frequency).map(([label, count]) => `${label} (${display(count)})`).join(", "));
  } else if (name === "episodes") {
    lines.push(`${display(metric.count)} episode(s); average resolved duration ${display(metric.average_duration_minutes)} minutes`);
  } else if (name === "timing") {
    const totals = { matched: 0, onTime: 0, early: 0, late: 0, unplanned: 0, missing: 0 };
    for (const rowValue of records(metric.values)) { const row = object(rowValue); totals.matched += Number(row.matched_count ?? 0); totals.onTime += Number(row.on_time ?? 0); totals.early += Number(row.early ?? 0); totals.late += Number(row.late ?? 0); totals.unplanned += Number(row.unplanned ?? 0); totals.missing += Number(row.missing_count ?? 0); }
    lines.push(`${totals.matched.toString()} matched: ${totals.onTime.toString()} on time, ${totals.early.toString()} early, ${totals.late.toString()} late`); lines.push(`${totals.unplanned.toString()} unmatched dose(s); ${totals.missing.toString()} unmatched plan slot(s)`);
  }
  return <article className="report-summary-card"><h4>{name.replaceAll("_", " ")}</h4>{lines.length === 0 ? <p>No concise summary available.</p> : <ul>{lines.map((line) => <li key={line}>{line}</li>)}</ul>}</article>;
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
  const categoryRecords = previewRecords(preview.snapshot_content[category], category);
  if (category === "ai" && !preview.include_ai) return null;
  return <section className={`category-card ${className}`} aria-labelledby={`report-${category}-heading`}><p className="category-label">{title}</p><h3 id={`report-${category}-heading`}>{title}</h3>{warning === undefined ? null : <p className="category-disclaimer">{warning}</p>}{categoryRecords.length === 0 ? <p>No selected records in this category.</p> : <div className="report-human-list">{categoryRecords.map((record, index) => { const summary = recordSummary(record); return <article key={`${category}-${index.toString()}`}><h4>{summary.title}</h4>{summary.details.filter(Boolean).map((detail) => <span key={detail}>{detail}</span>)}</article>; })}</div>}</section>;
}

function ReportPreviewPanel({ preview }: { preview: ReportPreview }): React.JSX.Element {
  const counts = Object.fromEntries(Object.entries(preview.snapshot_content).map(([key, value]) => [key, records(value).length]));
  return <section aria-labelledby="report-preview-heading"><h2 id="report-preview-heading">Snapshot preview</h2><p><strong>{preview.date_from}</strong> through <strong>{preview.date_to}</strong> in <strong>{timezoneAbbreviationForLocalDate(preview.timezone, preview.date_to)}</strong>.</p><aside className="safety-note">This frozen preview is organized for human review. Exact source IDs and machine-readable fields stay in optional JSON/CSV companions. Later corrections do not silently rewrite this snapshot.</aside><div className="report-preview-counts"><span><strong>{String(counts.fact ?? 0)}</strong> recorded facts</span><span><strong>{String(counts.plan ?? 0)}</strong> approved plans</span><span><strong>{String(counts.patient_note ?? 0)}</strong> patient notes</span></div><section className="metric-card"><h3>Period overview</h3>{Object.keys(preview.metric_values).length === 0 ? <p>No metrics selected.</p> : <div className="report-summary-grid">{Object.entries(preview.metric_values).map(([name, metric]) => <MetricPreview name={name} value={metric} key={name} />)}</div>}</section><CategoryPreview preview={preview} category="fact" title="Recorded facts" className="category-card--fact" /><CategoryPreview preview={preview} category="plan" title="Physician-approved plan" className="category-card--plan" warning="Approved plan content is separate from recorded actual doses." /><CategoryPreview preview={preview} category="patient_note" title="Patient notes and questions" className="category-card--patient" /><CategoryPreview preview={preview} category="ai" title="AI-generated analysis" className="category-card--ai" warning="Generated content—not a recorded fact or physician-approved instruction. Review against its cited sources." /><details className="report-audit-details"><summary>Snapshot audit details</summary><p>Checksum: <code>{preview.canonical_sha256}</code></p><p>Render version: {preview.render_version}</p></details></section>;
}

export function ReportsPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const [form, setForm] = useState(() => { const now = new Date(); return { dateFrom: localDate(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000), timezone), dateTo: localDate(now, timezone), timezone, sections: defaultSections, includeAi: false, includeSensitive: false, csv: false, json: false }; });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const historyView = useMemo(() => reportHistoryView(appliedSearch, timezone), [appliedSearch, timezone]);
  const historyFilters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: historyView.dateFrom, dateTo: historyView.dateTo, timezone: historyView.timezone }), [historyView.dateFrom, historyView.dateTo, historyView.timezone]);
  const [historyDraftState, setHistoryDraftState] = useState({ search: appliedSearch, filters: historyFilters });
  const historyDraft = historyDraftState.search === appliedSearch ? historyDraftState.filters : historyFilters;
  const setHistoryDraft = (filters: RecordedHistoryFilters): void => { setHistoryDraftState({ search: appliedSearch, filters }); };
  const invalidHistoryRange = historyFilters.dateFrom !== "" && historyFilters.dateTo !== "" && historyFilters.dateFrom > historyFilters.dateTo;
  const [historyValidation, setHistoryValidation] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const history = useQuery({ queryKey: ["reports", historyFilters, historyView.page], queryFn: () => getReports(historyFilters, historyView.page), enabled: !invalidHistoryRange });
  const preview = useQuery({ queryKey: ["report", selectedId], queryFn: () => getReport(selectedId ?? ""), enabled: selectedId !== null });
  const generation = useMutation({ mutationFn: createReport, onSuccess: async (created) => { setSelectedId(created.id); setSearchParams(reportHistorySearch({ ...historyView, page: 1 })); await queryClient.invalidateQueries({ queryKey: ["reports"] }); } });
  const dateError = reportDateError(form.dateFrom, form.dateTo);

  function toggleSection(value: ReportSection, checked: boolean): void { setForm({ ...form, sections: checked ? [...form.sections, value] : form.sections.filter((section) => section !== value) }); }
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void { event.preventDefault(); const payload: ReportCreate = { date_from: form.dateFrom, date_to: form.dateTo, timezone: form.timezone, selected_sections: form.sections, include_ai: form.includeAi, include_sensitive: form.includeSensitive, companion_formats: [...(form.csv ? ["csv" as const] : []), ...(form.json ? ["json" as const] : [])] }; generation.mutate(payload); }

  return <Page title="Reports" description="Build immutable, physician-ready snapshots from recorded facts and approved plan data.">
    <Alert color="orange" variant="light" title="Report boundary" role="note">A report describes selected records and deterministic metrics. It is not medical advice and does not establish causation. AI is excluded unless you opt in below.</Alert>
    <div className="report-layout">
      <section aria-labelledby="builder-heading">
        <Title order={2} id="builder-heading" mb="sm">Build a report</Title>
        <Paper component="form" className="report-builder" withBorder radius="md" p="lg" onSubmit={submit}>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput required type="date" label="From date" value={form.dateFrom} onChange={(event) => { setForm({ ...form, dateFrom: event.currentTarget.value }); }} />
            <TextInput required type="date" label="Through date" value={form.dateTo} onChange={(event) => { setForm({ ...form, dateTo: event.currentTarget.value }); }} />
          </SimpleGrid>
          <TextInput required label="IANA timezone" value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.currentTarget.value }); }} />
          {dateError === null ? null : <Alert color="red" role="alert">{dateError}</Alert>}
          <Fieldset legend="Sections">
            <Stack gap="sm">{sectionOptions.map(([value, label]) => <Checkbox key={value} label={label} checked={form.sections.includes(value)} onChange={(event) => { toggleSection(value, event.currentTarget.checked); }} />)}</Stack>
          </Fieldset>
          <Fieldset legend="Privacy and companions">
            <Stack gap="sm">
              <Checkbox label="Include separately labeled AI-generated analysis" checked={form.includeAi} onChange={(event) => { setForm({ ...form, includeAi: event.currentTarget.checked }); }} />
              {form.includeAi ? <Alert color="orange">AI content is generated, may be wrong, and will be boxed separately with source provenance.</Alert> : null}
              <Checkbox label="Include notes marked sensitive" checked={form.includeSensitive} onChange={(event) => { setForm({ ...form, includeSensitive: event.currentTarget.checked }); }} />
              <Checkbox label="Include CSV companion" checked={form.csv} onChange={(event) => { setForm({ ...form, csv: event.currentTarget.checked }); }} />
              <Checkbox label="Include JSON companion" checked={form.json} onChange={(event) => { setForm({ ...form, json: event.currentTarget.checked }); }} />
            </Stack>
          </Fieldset>
          <Button type="submit" loading={generation.isPending} disabled={form.sections.length === 0 || dateError !== null}>Generate immutable report</Button>
          {form.sections.length === 0 ? <Alert color="red" role="alert">Select at least one section.</Alert> : null}
          {generation.isError ? <Alert color="red" role="alert">The report could not be generated. Check the date range, timezone, and selected sections.</Alert> : null}
        </Paper>
      </section>
      <section aria-labelledby="history-heading">
        <Title order={2} id="history-heading" mb="sm">Snapshot history</Title>
        <Paper component="form" withBorder radius="md" p="lg" onSubmit={(event) => { event.preventDefault(); if (historyDraft.dateFrom !== "" && historyDraft.dateTo !== "" && historyDraft.dateFrom > historyDraft.dateTo) { setHistoryValidation("From date must be on or before Through date."); return; } setHistoryValidation(null); setSelectedId(null); setSearchParams(reportHistorySearch({ ...historyDraft, page: 1 })); }}>
          <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput type="date" label="Created from date" value={historyDraft.dateFrom} onChange={(event) => { setHistoryDraft({ ...historyDraft, dateFrom: event.currentTarget.value }); }} />
              <TextInput type="date" label="Created through date" value={historyDraft.dateTo} onChange={(event) => { setHistoryDraft({ ...historyDraft, dateTo: event.currentTarget.value }); }} />
            </SimpleGrid>
            <TextInput required label="History IANA timezone" value={historyDraft.timezone} onChange={(event) => { setHistoryDraft({ ...historyDraft, timezone: event.currentTarget.value }); }} />
            {historyValidation === null && !invalidHistoryRange ? null : <Alert color="red" role="alert">{historyValidation ?? "From date must be on or before Through date."}</Alert>}
            <Group><Button type="submit">Apply history filters</Button><Button variant="outline" type="button" onClick={() => { const reset = { dateFrom: "", dateTo: "", timezone }; setHistoryValidation(null); setSelectedId(null); setHistoryDraftState({ search: "", filters: reset }); setSearchParams(new URLSearchParams()); }}>Clear history filters</Button></Group>
          </Stack>
        </Paper>
        <Text c="dimmed" size="sm" mt="sm">Inclusive history dates use {timezoneAbbreviation(historyFilters.timezone)} and filter immutable snapshots by creation time, not by the report’s covered clinical dates.</Text>
        {history.isFetching ? <Text role="status" mt="md">Loading report history…</Text> : null}
        {history.isError ? <Alert color="red" role="alert" mt="md">Report history could not be loaded.</Alert> : null}
        {history.data?.page.total_items === 0 ? <Paper withBorder radius="md" p="lg" mt="md"><Title order={3}>{historyFilters.dateFrom === "" && historyFilters.dateTo === "" ? "No report snapshots yet" : "No report snapshots match"}</Title><Text>{historyFilters.dateFrom === "" && historyFilters.dateTo === "" ? "Choose a range and sections to create the first immutable report." : "Change the history filters or create a new immutable report."}</Text></Paper> : null}
        {history.data === undefined || history.data.items.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Immutable report snapshot history table"><table><caption>Immutable report snapshots ordered by creation time, latest first.</caption><thead><tr><th scope="col">Created</th><th scope="col">Covered dates</th><th scope="col">Privacy content</th><th scope="col">Preview and downloads</th></tr></thead><tbody>{history.data.items.map((report) => <tr key={report.id}><td><time dateTime={report.created_at}>{new Date(report.created_at).toLocaleString()}</time></td><th scope="row">{report.date_from} through {report.date_to}<span>{timezoneAbbreviationForLocalDate(report.timezone, report.date_to)}</span></th><td>{report.include_ai ? "Includes separately labeled AI" : "AI excluded"}</td><td><Group gap="xs" align="stretch"><Button type="button" variant="outline" onClick={() => { setSelectedId(report.id); }}>Preview snapshot</Button>{report.artifacts.map((artifact) => <Button component="a" variant="light" href={artifact.download_url} key={artifact.format}>Download {artifact.format.toUpperCase()}</Button>)}</Group></td></tr>)}</tbody></table></div>}
        {history.data === undefined ? null : <PaginationControls label="Report history" metadata={history.data.page} onPageChange={(page) => { setSelectedId(null); setSearchParams(reportHistorySearch({ ...historyView, page })); }} />}
      </section>
    </div>
    {preview.isPending && selectedId !== null ? <Text role="status">Loading immutable snapshot…</Text> : null}
    {preview.isError ? <Alert color="red" role="alert">The selected snapshot could not be loaded or failed its integrity check.</Alert> : null}
    {preview.data === undefined ? null : <ReportPreviewPanel preview={preview.data} />}
  </Page>;
}
