import { Alert, Button, Checkbox, Group, NativeSelect, Paper, SimpleGrid, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getTimeline, type TimelineFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { HistoryDateShortcuts } from "../components/HistoryDateShortcuts";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatQuantitativeText } from "../format";
import { defaultHistoryDateRange, historyDateRangeFromSearch, setHistoryDateRange, type HistoryDateRange } from "../historyDates";
import { timezoneAbbreviation } from "../time";

const eventTypes = [
  ["", "All record types"],
  ["dose", "Doses"],
  ["symptom", "Symptoms"],
  ["diary", "Diary"],
  ["life_event", "Life events"],
  ["emergency_injection", "Emergency injections"],
  ["context", "Environmental context"],
  ["blood_pressure", "Blood pressure"],
  ["weight", "Weight"],
  ["temperature", "Body temperature"],
] as const;

function localTime(value: string): string {
  return value.replace("T", " ").slice(0, 16);
}

function words(value: string): string {
  return value.replaceAll("_", " ");
}

function categoryLabel(category: "fact" | "plan" | "ai", eventType: string): string {
  if (eventType === "context") return "Environmental context";
  if (category === "plan") return "Physician-approved plan";
  if (category === "ai") return "AI-generated observation";
  return "Recorded fact";
}

function categoryNote(category: "fact" | "plan" | "ai", eventType: string): string | null {
  if (eventType === "context") return "Context only—not a symptom, dose, physician instruction, or AI conclusion.";
  if (category === "plan") return "Approved plan—not an actual recorded event.";
  if (category === "ai") return "Generated analysis—not a recorded fact or physician-approved plan.";
  return null;
}

interface TimelineView extends TimelineFilters, HistoryDateRange {}

function defaultFilters(timezone: string): TimelineView {
  return { type: "", ...defaultHistoryDateRange(timezone), timezone, includeSensitive: false, sortOrder: "desc", page: 1 };
}

function filtersFromSearch(search: string, profileTimezone: string): TimelineView {
  const params = new URLSearchParams(search);
  const timezone = params.get("timezone") ?? profileTimezone;
  return {
    type: params.get("types") ?? "",
    ...historyDateRangeFromSearch(params, timezone),
    timezone,
    includeSensitive: params.get("include_sensitive") === "true",
    sortOrder: params.get("sort_order") === "asc" ? "asc" : "desc",
    page: /^\d+$/.test(params.get("page") ?? "") && Number(params.get("page")) >= 1 ? Number(params.get("page")) : 1,
  };
}

function searchFromFilters(filters: TimelineView): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.type !== "") params.set("types", filters.type);
  setHistoryDateRange(params, filters);
  params.set("timezone", filters.timezone);
  if (filters.includeSensitive) params.set("include_sensitive", "true");
  params.set("sort_order", filters.sortOrder);
  if (filters.page > 1) params.set("page", filters.page.toString());
  return params;
}

export function TimelinePage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const emptyFilters = useMemo(() => defaultFilters(timezone), [timezone]);
  const filters = useMemo(() => filtersFromSearch(appliedSearch, timezone), [appliedSearch, timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: TimelineView): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const timeline = useQuery({ queryKey: ["timeline", filters], queryFn: () => getTimeline(filters) });
  const filtered = filters.type !== "" || filters.dateFrom !== "" || filters.dateTo !== "" || filters.includeSensitive;
  const selectedHealthCurveDay = filters.dateFrom !== "" && filters.dateFrom === filters.dateTo ? filters.dateFrom : null;
  const healthCurveUrl = selectedHealthCurveDay === null ? "/healthcurve" : `/healthcurve?${new URLSearchParams({ day: selectedHealthCurveDay, timezone: filters.timezone }).toString()}`;

  return (
    <Page title="Timeline" description="The authoritative chronology of recorded facts, with source and correction provenance.">
      <Paper component="form" className="filter-panel timeline-filter-panel" withBorder radius="lg" p={{ base: "md", sm: "lg" }} onSubmit={(event) => { event.preventDefault(); setSearchParams(searchFromFilters({ ...draft, page: 1 })); }}>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} className="timeline-filter-grid">
          <NativeSelect label="Record type" value={draft.type} onChange={(event) => { setDraft({ ...draft, type: event.target.value }); }}>
            {eventTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
          <TextInput label="From date" type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value, allHistory: false }); }} />
          <TextInput label="Through date" type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value, allHistory: false }); }} />
          <NativeSelect label="Order" value={draft.sortOrder} onChange={(event) => { setDraft({ ...draft, sortOrder: event.target.value as TimelineFilters["sortOrder"] }); }}>
            <option value="desc">Latest first</option>
            <option value="asc">Oldest first</option>
          </NativeSelect>
        </SimpleGrid>
        <Checkbox label="Include sensitive diary entries" checked={draft.includeSensitive} onChange={(event) => { setDraft({ ...draft, includeSensitive: event.target.checked }); }} />
        <Group className="filter-actions">
          <Button type="submit">Apply filters</Button>
          <Button variant="outline" type="button" onClick={() => { const allHistory = { ...emptyFilters, dateFrom: "", dateTo: "", allHistory: true }; setDraftState({ search: "", filters: allHistory }); setSearchParams(searchFromFilters(allHistory)); }}>Clear filters</Button>
        </Group>
        <HistoryDateShortcuts dateFrom={draft.dateFrom} dateTo={draft.dateTo} timezone={draft.timezone} label="Quick timeline dates" onSelect={(day) => { const selected = { ...draft, dateFrom: day, dateTo: day, allHistory: false, page: 1 }; setDraftState({ search: "", filters: selected }); setSearchParams(searchFromFilters(selected)); }} />
      </Paper>
      {filters.allHistory ? <Alert color="blue"><strong>Showing all history.</strong> Choose dates or a quick date to bound the timeline again.</Alert> : null}
      <Group justify="space-between" align="center" mt="md">
        <Text c="dimmed" size="sm">Sensitive diary entries are hidden by default. Dates use {timezoneAbbreviation(timezone)}.</Text>
        <Button component={Link} variant="outline" to={healthCurveUrl}>{selectedHealthCurveDay === null ? "Open HealthCurve daily review" : `Review ${selectedHealthCurveDay} in HealthCurve`}</Button>
      </Group>

      {timeline.isPending ? <Text mt="lg" role="status">Loading timeline…</Text> : null}
      {timeline.isError ? <Alert color="red" mt="lg" role="alert">The timeline could not be loaded.</Alert> : null}
      {timeline.data?.items.length === 0 ? (
        <Paper component="section" className="empty-state" withBorder radius="lg" p="lg">
          <Title order={2}>{filtered ? "No records match these filters" : "No records yet"}</Title>
          <Text mt="xs">{filtered ? "Change or clear the filters to see other recorded facts." : "New facts will appear here after they are recorded."}</Text>
        </Paper>
      ) : null}
      {timeline.data === undefined || timeline.data.items.length === 0 ? null : (
        <Paper className="timeline-table-region" withBorder radius="lg" mt="lg">
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Timeline records table">
          <Table className="timeline-table" verticalSpacing="sm" horizontalSpacing="md" highlightOnHover>
            <caption>Timeline records ordered by experienced time, {filters.sortOrder === "asc" ? "earliest first" : "latest first"}.</caption>
            <thead><tr><th scope="col">Experienced time</th><th scope="col">Record</th><th scope="col">Source and status</th><th scope="col">Provenance</th></tr></thead>
            <tbody>{timeline.data.items.map((item) => {
              const note = categoryNote(item.category, item.event_type);
              return <tr key={item.id} data-category={item.event_type === "context" ? "context" : item.category}>
                <td data-label="Experienced time" className="timeline-time"><time dateTime={item.time.occurred_at}>{localTime(item.time.local_time)}</time><span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td>
                <th data-label="Record" scope="row"><span className="timeline-summary">{formatQuantitativeText(item.summary)}</span><span className="timeline-type">{words(item.event_type)}</span></th>
                <td data-label="Source and status"><Stack gap={2}><span>{item.provenance?.source_type === undefined ? "Source not available" : words(item.provenance.source_type)}</span><span>{item.provenance?.confirmation_state === undefined ? "Confirmation not available" : words(item.provenance.confirmation_state)}</span></Stack></td>
                <td data-label="Provenance"><Stack gap={2}><span className={`timeline-category timeline-category--${item.event_type === "context" ? "context" : item.category}`}>{categoryLabel(item.category, item.event_type)}</span><span>{item.provenance?.is_correction === true ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span>{note === null ? null : <span className="timeline-category-note">{note}</span>}</Stack></td>
              </tr>;
            })}</tbody>
          </Table>
          </div>
        </Paper>
      )}
      {timeline.data === undefined ? null : <PaginationControls label="Timeline records" metadata={timeline.data.page} onPageChange={(page) => { setSearchParams(searchFromFilters({ ...filters, page })); }} />}
    </Page>
  );
}
