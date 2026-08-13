import { Alert, Button, Checkbox, Group, Paper, SimpleGrid, Text, TextInput, Textarea } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { correctSymptom, createSymptom, getDiaryEntries, getLifeEvents, getSymptoms, type Symptom, type SymptomCorrectionInput, type SymptomInput, type SymptomsDiaryFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { HistoryDateShortcuts } from "../components/HistoryDateShortcuts";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { historyDateRangeFromSearch, setHistoryDateRange, type HistoryDateRange } from "../historyDates";
import { timezoneAbbreviation } from "../time";

function localTime(value: string): string { return value.replace("T", " ").slice(0, 16); }

function nowLocal(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function words(value: string): string { return value.replaceAll("_", " "); }

interface ViewState extends SymptomsDiaryFilters, HistoryDateRange { symptomPage: number; diaryPage: number; lifePage: number }

function pageNumber(params: URLSearchParams, name: string): number {
  const value = params.get(name) ?? "";
  return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : 1;
}

function stateFromSearch(search: string, profileTimezone: string): ViewState {
  const params = new URLSearchParams(search);
  const timezone = params.get("timezone") ?? profileTimezone;
  return {
    ...historyDateRangeFromSearch(params, timezone),
    timezone,
    includeSensitive: params.get("include_sensitive") === "true",
    symptomPage: pageNumber(params, "symptom_page"),
    diaryPage: pageNumber(params, "diary_page"),
    lifePage: pageNumber(params, "life_page"),
  };
}

function searchFromState(state: ViewState): URLSearchParams {
  const params = new URLSearchParams({ timezone: state.timezone });
  setHistoryDateRange(params, state);
  if (state.includeSensitive) params.set("include_sensitive", "true");
  if (state.symptomPage > 1) params.set("symptom_page", state.symptomPage.toString());
  if (state.diaryPage > 1) params.set("diary_page", state.diaryPage.toString());
  if (state.lifePage > 1) params.set("life_page", state.lifePage.toString());
  return params;
}

function SymptomCorrectionForm({ symptom, close }: { symptom: Symptom; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [name, setName] = useState(symptom.name);
  const [severity, setSeverity] = useState(symptom.severity?.toString() ?? "");
  const [bodyArea, setBodyArea] = useState(symptom.body_area ?? "");
  const [notes, setNotes] = useState(symptom.notes ?? "");
  const [local, setLocal] = useState(symptom.time.local_time.slice(0, 19));
  const [timezone, setTimezone] = useState(symptom.time.timezone);
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: SymptomCorrectionInput) => correctSymptom(symptom.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["symptoms"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const changes: SymptomCorrectionInput["changes"] = {};
    if (name !== symptom.name) changes.name = name;
    if (severity !== (symptom.severity?.toString() ?? "")) changes.severity = severity === "" ? null : Number(severity);
    if (bodyArea !== (symptom.body_area ?? "")) changes.body_area = bodyArea === "" ? null : bodyArea;
    if (notes !== (symptom.notes ?? "")) changes.notes = notes === "" ? null : notes;
    if (local !== symptom.time.local_time.slice(0, 19) || timezone !== symptom.time.timezone) changes.time = { local_time: local, timezone };
    if (reason.trim() === "") { setValidation("Explain why this recorded fact needs correction."); return; }
    if (Object.keys(changes).length === 0) { setValidation("Change at least one recorded field."); return; }
    setValidation(null);
    mutation.mutate({ reason: reason.trim(), changes });
  }

  return <form aria-label={`Correct ${symptom.name} symptom`} onSubmit={submit}><Paper className="correction-form" p="md" radius="md">
    <Alert className="form-wide" color="orange">This creates a corrected fact and preserves the original.</Alert>
    <TextInput label="Name" required aria-label="Name" value={name} onChange={(event) => { setName(event.target.value); }} />
    <TextInput label="Severity (0–10)" type="number" min="0" max="10" value={severity} onChange={(event) => { setSeverity(event.target.value); }} />
    <TextInput label="Body area" value={bodyArea} onChange={(event) => { setBodyArea(event.target.value); }} />
    <TextInput label="Experienced local time" required aria-label="Experienced local time" type="datetime-local" step="1" value={local} onChange={(event) => { setLocal(event.target.value); }} />
    <TextInput label="Timezone" required aria-label="Timezone" value={timezone} onChange={(event) => { setTimezone(event.target.value); }} />
    <Textarea className="form-wide" label="Notes" value={notes} onChange={(event) => { setNotes(event.target.value); }} />
    <TextInput className="form-wide" label="Correction reason" required aria-label="Correction reason" value={reason} onChange={(event) => { setReason(event.target.value); }} />
    {validation === null ? null : <Alert className="form-wide" color="red" role="alert">{validation}</Alert>}
    {mutation.isError ? <Alert className="form-wide" color="red" role="alert">The correction was not saved. Check the values and timezone.</Alert> : null}
    <Group className="form-wide"><Button type="submit" loading={mutation.isPending}>Save corrected fact</Button><Button type="button" variant="outline" onClick={close}>Cancel</Button></Group>
  </Paper></form>;
}

function SymptomCreateForm({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const initial = { name: "", severity: "", bodyArea: "", localTime: nowLocal(), timezone, notes: "" };
  const [form, setForm] = useState(initial);
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (payload: SymptomInput) => createSymptom(payload),
    onSuccess: async () => {
      setForm({ ...initial, localTime: nowLocal() });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["symptoms"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline"] }),
        queryClient.invalidateQueries({ queryKey: ["daily-healthcurve"] }),
        queryClient.invalidateQueries({ queryKey: ["analytics"] }),
      ]);
    },
  });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const name = form.name.trim();
    const bodyArea = form.bodyArea.trim();
    const notes = form.notes.trim();
    if (name === "") { setValidation("Enter the symptom you experienced."); return; }
    setValidation(null);
    mutation.mutate({
      name,
      severity: form.severity === "" ? null : Number(form.severity),
      body_area: bodyArea === "" ? null : bodyArea,
      time: { local_time: form.localTime, timezone: form.timezone, fold: null },
      ended_at: null,
      episode_id: null,
      notes: notes === "" ? null : notes,
    });
  }

  return <form aria-label="Record a symptom" onSubmit={submit}><Paper className="correction-form" withBorder p="lg" radius="lg">
    <Alert className="form-wide" color="orange"><strong>Record what you experienced.</strong> This creates a subjective health fact, not a diagnosis or an explanation of its cause.</Alert>
    <TextInput id="new-symptom-name" label="Symptom" required aria-label="Symptom" maxLength={120} value={form.name} onChange={(event) => { setForm({ ...form, name: event.target.value }); }} placeholder="For example: fatigue, dizziness, or nausea" description="Use a short description of what you felt. Record each distinct symptom separately." />
    <TextInput id="new-symptom-severity" label="Severity (0–10)" type="number" min="0" max="10" inputMode="numeric" value={form.severity} onChange={(event) => { setForm({ ...form, severity: event.target.value }); }} description="Optional personal rating, where 0 is no noticeable impact and 10 is your most severe. Missing is kept as unknown, not zero." />
    <TextInput id="new-symptom-area" label="Body area" maxLength={120} value={form.bodyArea} onChange={(event) => { setForm({ ...form, bodyArea: event.target.value }); }} placeholder="Optional, such as head or abdomen" description="Where you noticed it, if a location is useful." />
    <TextInput id="new-symptom-time" label="Experienced local time" required aria-label="Experienced local time" type="datetime-local" step="1" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} description="When you experienced the symptom, using your best known local date and time." />
    <TextInput id="new-symptom-timezone" label="Symptom IANA timezone" required aria-label="Symptom IANA timezone" value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} description="Usually leave your profile timezone. Change it only if this local time occurred in another region." />
    <Textarea id="new-symptom-notes" className="form-wide" label="Notes" maxLength={2000} value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} description="Optional observations you want preserved with this symptom fact." />
    {validation === null ? null : <Alert className="form-wide" color="red" role="alert">{validation}</Alert>}
    {mutation.isError ? <Alert className="form-wide" color="red" role="alert">The symptom was not saved. Check the values, local time, and IANA timezone; your entries remain in the form.</Alert> : null}
    {mutation.isSuccess ? <Alert className="form-wide" color="green" role="status">Symptom recorded.</Alert> : null}
    <Button loading={mutation.isPending} type="submit">Record symptom</Button>
  </Paper></form>;
}

export function SymptomsDiaryPage(): React.JSX.Element {
  const { session } = useAuth();
  const profileTimezone = session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => stateFromSearch(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<SymptomsDiaryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone, includeSensitive: view.includeSensitive }), [view.dateFrom, view.dateTo, view.timezone, view.includeSensitive]);
  const appliedDraft = useMemo(() => ({ ...filters, allHistory: view.allHistory }), [filters, view.allHistory]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters: appliedDraft });
  const draft = draftState.search === appliedSearch ? draftState.filters : appliedDraft;
  const setDraft = (next: SymptomsDiaryFilters & HistoryDateRange): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const symptoms = useQuery({ queryKey: ["symptoms", filters, view.symptomPage], queryFn: () => getSymptoms(filters, view.symptomPage), enabled: !invalidRange });
  const diary = useQuery({ queryKey: ["diary", filters, view.diaryPage], queryFn: () => getDiaryEntries(filters, view.diaryPage), enabled: !invalidRange });
  const life = useQuery({ queryKey: ["life-events", filters, view.lifePage], queryFn: () => getLifeEvents(filters, view.lifePage), enabled: !invalidRange });
  const currentSymptoms = symptoms.data?.items ?? [];
  const symptomById = new Map([...currentSymptoms, ...(symptoms.data?.revisions ?? [])].map((item) => [item.id, item]));

  function historyFor(item: Symptom): Symptom[] { const history: Symptom[] = []; let id = item.provenance.supersedes_id ?? null; while (id !== null) { const prior = symptomById.get(id); if (prior === undefined) break; history.push(prior); id = prior.provenance.supersedes_id ?? null; } return history; }
  const loading = symptoms.isFetching || diary.isFetching || life.isFetching;
  const failed = symptoms.isError || diary.isError || life.isError;

  return <Page title="Symptoms & diary" description="Subjective symptoms, diary notes, and life events are recorded facts—not diagnoses or causal claims.">
    <Paper component="form" className="filter-panel" withBorder p="lg" radius="lg" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setEditing(null); setSearchParams(searchFromState({ ...draft, symptomPage: 1, diaryPage: 1, lifePage: 1 })); }}>
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md"><TextInput label="From date" type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value, allHistory: false }); }} /><TextInput label="Through date" type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value, allHistory: false }); }} /><TextInput label="IANA timezone" required aria-label="IANA timezone" value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></SimpleGrid>
      <Checkbox mt="md" label="Reveal sensitive diary and life-event entries" checked={draft.includeSensitive} onChange={(event) => { setDraft({ ...draft, includeSensitive: event.target.checked }); }} />
      {validation === null && !invalidRange ? null : <Alert color="red" mt="md" role="alert">{validation ?? "From date must be on or before Through date."}</Alert>}
      <div className="filter-footer-row"><Group><Button type="submit">Apply filters</Button><Button variant="outline" type="button" onClick={() => { const allHistory = { dateFrom: "", dateTo: "", timezone: profileTimezone, includeSensitive: false, allHistory: true, symptomPage: 1, diaryPage: 1, lifePage: 1 }; setValidation(null); setEditing(null); setDraftState({ search: "", filters: allHistory }); setSearchParams(searchFromState(allHistory)); }}>Clear filters</Button></Group>
      <HistoryDateShortcuts dateFrom={draft.dateFrom} dateTo={draft.dateTo} timezone={draft.timezone} label="Quick symptom and diary dates" onSelect={(day) => { const selected = { ...view, ...draft, dateFrom: day, dateTo: day, allHistory: false, symptomPage: 1, diaryPage: 1, lifePage: 1 }; setValidation(null); setEditing(null); setDraftState({ search: "", filters: selected }); setSearchParams(searchFromState(selected)); }} /></div>
    </Paper>
    {view.allHistory ? <Alert color="blue" mt="md"><strong>Showing all history.</strong> Choose dates or a quick date to bound these records again.</Alert> : null}
    <Text c="dimmed" my="md">Inclusive dates use {timezoneAbbreviation(filters.timezone)}. Sensitive text is hidden by default and appears only after applying the reveal control.</Text>
    {loading ? <Text role="status">Loading recorded symptoms and notes…</Text> : null}
    {failed ? <Alert color="red" role="alert">Some recorded facts could not be loaded.</Alert> : null}

    <section aria-labelledby="symptoms-heading"><h2 id="symptoms-heading">Symptoms</h2>
      <h3>Record a symptom</h3>
      <SymptomCreateForm timezone={profileTimezone} />
      <h3>Recorded symptoms</h3>
      {symptoms.data?.page.total_items === 0 ? <p>No symptoms recorded.</p> : null}
      {currentSymptoms.length === 0 ? null : <div className="table-scroll symptom-table-region" tabIndex={0} role="region" aria-label="Symptom records table"><table className="symptom-records-table"><caption>Current symptom facts, latest experienced time first, with correction history.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Symptom</th><th scope="col">Severity and area</th><th scope="col">Source and status</th><th scope="col">Notes and actions</th></tr></thead><tbody>{currentSymptoms.flatMap((item) => { const history = historyFor(item); const row = <tr key={item.id}><td data-label="Experienced time" className="timeline-time">{localTime(item.time.local_time)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th data-label="Symptom" scope="row">{item.name}</th><td data-label="Severity and area">{item.severity === null ? "Not recorded" : `${item.severity.toString()}/10`}{item.body_area === null ? null : <span>{item.body_area}</span>}</td><td data-label="Source and status" className="symptom-records-table__provenance"><span>{words(item.provenance.source_type)}</span><span>{words(item.provenance.confirmation_state)}</span><span>{item.provenance.is_correction ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span></td><td data-label="Notes and actions" className="symptom-records-table__actions"><span>{item.notes ?? <span className="missing-value">None</span>}</span><Button mt="sm" type="button" onClick={() => { setEditing(editing === item.id ? null : item.id); }}>{editing === item.id ? "Close correction form" : "Correct recorded symptom"}</Button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><p>{prior.name}{prior.severity === null ? "" : ` · severity ${prior.severity.toString()}/10`} · {localTime(prior.time.local_time)} · {timezoneAbbreviation(prior.time.timezone, prior.time.occurred_at)}</p>{prior.body_area === null ? null : <p>Body area: {prior.body_area}</p>}{prior.notes === null ? null : <p>Notes: {prior.notes}</p>}</article>)}</details>}</td></tr>; return editing === item.id ? [row, <tr className="correction-table-row" key={`${item.id}-correction`}><td colSpan={5}><SymptomCorrectionForm symptom={item} close={() => { setEditing(null); }} /></td></tr>] : [row]; })}</tbody></table></div>}
      {symptoms.data === undefined ? null : <PaginationControls label="Symptom records" metadata={symptoms.data.page} onPageChange={(symptomPage) => { setEditing(null); setSearchParams(searchFromState({ ...view, symptomPage })); }} />}
    </section>

    <section aria-labelledby="diary-heading"><h2 id="diary-heading">Diary</h2>
      {diary.data?.page.total_items === 0 ? <p>No {filters.includeSensitive ? "" : "non-sensitive "}diary entries match.</p> : null}
      {diary.data === undefined || diary.data.items.length === 0 ? null : <div className="table-scroll symptom-table-region" tabIndex={0} role="region" aria-label="Diary records table"><table className="symptom-records-table"><caption>Recorded diary facts. Sensitive text appears only when explicitly revealed.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Entry</th><th scope="col">Privacy and tags</th><th scope="col">Source and confirmation</th></tr></thead><tbody>{diary.data.items.map((item) => <tr key={item.id}><td data-label="Experienced time" className="timeline-time">{localTime(item.time.local_time)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th data-label="Entry" scope="row">{item.text}</th><td data-label="Privacy and tags">{item.is_sensitive ? "Sensitive" : "Not marked sensitive"}{item.tags === null || item.tags.length === 0 ? null : <span>{item.tags}</span>}</td><td data-label="Source and confirmation"><span>{words(item.provenance.source_type)}</span><span>{words(item.provenance.confirmation_state)}</span></td></tr>)}</tbody></table></div>}
      {diary.data === undefined ? null : <PaginationControls label="Diary records" metadata={diary.data.page} onPageChange={(diaryPage) => { setSearchParams(searchFromState({ ...view, diaryPage })); }} />}
    </section>

    <section aria-labelledby="life-heading"><h2 id="life-heading">Life events</h2>
      {life.data?.page.total_items === 0 ? <p>No {filters.includeSensitive ? "" : "non-sensitive "}life events match.</p> : null}
      {life.data === undefined || life.data.items.length === 0 ? null : <div className="table-scroll symptom-table-region" tabIndex={0} role="region" aria-label="Life event records table"><table className="symptom-records-table"><caption>Recorded life-event facts. Sensitive text appears only when explicitly revealed.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Event</th><th scope="col">Category and description</th><th scope="col">Privacy, source, and confirmation</th></tr></thead><tbody>{life.data.items.map((item) => <tr key={item.id}><td data-label="Experienced time" className="timeline-time">{localTime(item.time.local_time)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th data-label="Event" scope="row">{item.title}</th><td data-label="Category and description"><span>{words(item.life_category)}</span>{item.description === null ? <span className="missing-value">No description</span> : <span>{item.description}</span>}</td><td data-label="Privacy, source, and confirmation"><span>{item.is_sensitive ? "Sensitive" : "Not marked sensitive"}</span><span>{words(item.provenance.source_type)}</span><span>{words(item.provenance.confirmation_state)}</span></td></tr>)}</tbody></table></div>}
      {life.data === undefined ? null : <PaginationControls label="Life event records" metadata={life.data.page} onPageChange={(lifePage) => { setSearchParams(searchFromState({ ...view, lifePage })); }} />}
    </section>
  </Page>;
}
