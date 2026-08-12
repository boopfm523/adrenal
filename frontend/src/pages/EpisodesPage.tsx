import { Alert, Button, Group, NativeSelect, Paper, SimpleGrid, Text, TextInput, Textarea, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { createEpisode, getEmergencyInjections, getEpisodes, updateEpisode, type Episode, type Injection, type RecordedHistoryFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { HistoryDateShortcuts } from "../components/HistoryDateShortcuts";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatDecimal, formatMeasurement } from "../format";
import { historyDateRangeFromSearch, setHistoryDateRange, type HistoryDateRange } from "../historyDates";
import { timezoneAbbreviation } from "../time";

function nowLocal(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function displayTime(value: string, timezone: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: timezone }).format(new Date(value));
}

function optional(value: FormDataEntryValue | null): string | null {
  const text = typeof value === "string" ? value.trim() : "";
  return text === "" ? null : text;
}

interface EpisodeViewState extends RecordedHistoryFilters, HistoryDateRange { openPage: number; historyPage: number; injectionPage: number; reviewEpisode?: string | null }

function pageNumber(params: URLSearchParams, name: string): number { const value = params.get(name) ?? ""; return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : 1; }
function viewFromSearch(search: string, profileTimezone: string): EpisodeViewState { const params = new URLSearchParams(search); const timezone = params.get("timezone") ?? profileTimezone; return { ...historyDateRangeFromSearch(params, timezone), timezone, openPage: pageNumber(params, "open_page"), historyPage: pageNumber(params, "history_page"), injectionPage: pageNumber(params, "injection_page"), reviewEpisode: params.get("review_episode") }; }
function searchFromView(view: EpisodeViewState): URLSearchParams { const params = new URLSearchParams({ timezone: view.timezone }); setHistoryDateRange(params, view); if (view.openPage > 1) params.set("open_page", view.openPage.toString()); if (view.historyPage > 1) params.set("history_page", view.historyPage.toString()); if (view.injectionPage > 1) params.set("injection_page", view.injectionPage.toString()); if (view.reviewEpisode != null) params.set("review_episode", view.reviewEpisode); return params; }

function EpisodeRow({ episode, review = false }: { episode: Episode; review?: boolean }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const row = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    if (!review) return;
    row.current?.scrollIntoView({ block: "center" });
    row.current?.focus({ preventScroll: true });
  }, [review]);
  const mutation = useMutation({
    mutationFn: (payload: Parameters<typeof updateEpisode>[1]) => updateEpisode(episode.id, payload),
    onSuccess: async () => { setEditing(false); await queryClient.invalidateQueries({ queryKey: ["episodes"] }); },
  });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    mutation.mutate({
      severity: optional(data.get("severity")) as Episode["severity"],
      highest_temperature_c: optional(data.get("temperature")),
      illness_description: optional(data.get("illness")),
      notes: optional(data.get("notes")),
      recovery_notes: optional(data.get("recovery")),
      outcome: optional(data.get("outcome")),
    });
  }

  function close(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    mutation.mutate({ status: "resolved", ended_at: { local_time: data.get("ended_at") as string, timezone: episode.timezone, fold: null } });
  }

  return <><tr ref={row} id={`episode-${episode.id}`} className={review ? "episode-review-target" : undefined} tabIndex={review ? -1 : undefined}><td data-label="Started" className="timeline-time"><time dateTime={episode.started_at}>{displayTime(episode.started_at, episode.timezone)}</time><span>{timezoneAbbreviation(episode.timezone, episode.started_at)}</span></td><th data-label="Trigger" scope="row">{episode.trigger}</th><td data-label="Status and duration"><span className={`episode-status episode-status--${episode.status}`}>{episode.status}</span><span>Severity: {episode.severity ?? "not recorded"}</span><span>{episode.ended_at === null ? "Episode remains open" : `Ended ${displayTime(episode.ended_at, episode.timezone)}`}</span></td><td data-label="Linked facts"><span>{formatDecimal(episode.dose_count)} linked dose{episode.dose_count === 1 ? "" : "s"}</span><span>{formatDecimal(episode.symptom_count)} linked symptom{episode.symptom_count === 1 ? "" : "s"}</span></td><td data-label="Context and actions">{episode.illness_description === null ? null : <span>Context: {episode.illness_description}</span>}{episode.highest_temperature_c === null ? null : <span>Highest temperature: {formatDecimal(episode.highest_temperature_c)} °C</span>}{episode.notes === null ? null : <span>Notes: {episode.notes}</span>}{episode.recovery_notes === null ? null : <span>Recovery: {episode.recovery_notes}</span>}{episode.outcome === null ? null : <span>Outcome: {episode.outcome}</span>}{episode.status !== "open" ? null : <Button mt="sm" variant="outline" type="button" onClick={() => { setEditing(!editing); }}>{editing ? "Cancel changes" : "Add or update context"}</Button>}</td></tr>
    {episode.status !== "open" ? null : <tr className="correction-table-row"><td colSpan={5}>{!editing ? null : <form onSubmit={submit}><Paper className="correction-form" p="md" radius="md"><NativeSelect label="Severity" name="severity" defaultValue={episode.severity ?? ""} data={[{ value: "", label: "Not recorded" }, { value: "mild", label: "Mild" }, { value: "moderate", label: "Moderate" }, { value: "severe", label: "Severe" }]} /><TextInput label="Highest temperature (°C)" name="temperature" type="number" min="25" max="45" step="0.1" defaultValue={episode.highest_temperature_c ?? ""} /><Textarea className="form-wide" label="Illness or stress context" name="illness" defaultValue={episode.illness_description ?? ""} /><Textarea className="form-wide" label="Notes" name="notes" defaultValue={episode.notes ?? ""} /><Textarea className="form-wide" label="Recovery notes" name="recovery" defaultValue={episode.recovery_notes ?? ""} /><Textarea className="form-wide" label="Outcome" name="outcome" defaultValue={episode.outcome ?? ""} /><Button loading={mutation.isPending} type="submit">Save context</Button></Paper></form>}<form className="episode-close-form" onSubmit={close}><Group align="end"><TextInput label="Episode end time" name="ended_at" type="datetime-local" required defaultValue={nowLocal()} /><Button loading={mutation.isPending} type="submit">Close episode</Button></Group></form>{mutation.isError ? <Alert color="red" role="alert">The episode could not be updated. Check the time and try again.</Alert> : null}</td></tr>}
  </>;
}

function EpisodeTable({ episodes, label, reviewEpisode = null }: { episodes: Episode[]; label: string; reviewEpisode?: string | null | undefined }): React.JSX.Element { return <div className="table-scroll episode-table-region" tabIndex={0} role="region" aria-label={`${label} table`}><table className="episode-table"><caption>{label}, ordered by episode start time, latest first.</caption><thead><tr><th scope="col">Started</th><th scope="col">Trigger</th><th scope="col">Status and duration</th><th scope="col">Linked recorded facts</th><th scope="col">Recorded context and actions</th></tr></thead><tbody>{episodes.map((episode) => <EpisodeRow key={episode.id} episode={episode} review={episode.id === reviewEpisode} />)}</tbody></table></div>; }

function InjectionTable({ injections }: { injections: Injection[] }): React.JSX.Element { return <div className="table-scroll episode-table-region" tabIndex={0} role="region" aria-label="Emergency injection records table"><table className="episode-table"><caption>Recorded emergency injection facts, latest experienced time first.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Amount and route</th><th scope="col">Reason and response</th><th scope="col">Emergency follow-up</th><th scope="col">Source and confirmation</th></tr></thead><tbody>{injections.map((item) => <tr key={item.id}><td data-label="Experienced time" className="timeline-time">{item.time.local_time.replace("T", " ").slice(0, 16)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th data-label="Amount and route" scope="row">{formatMeasurement(item.amount, item.unit)}<span>{item.route.replaceAll("_", " ")}</span></th><td data-label="Reason and response"><span>{item.reason ?? "Reason not recorded"}</span><span>{item.response ?? "Response not recorded"}</span></td><td data-label="Emergency follow-up"><span>Emergency services: {item.emergency_services_called === null ? "not recorded" : item.emergency_services_called ? "called" : "not called"}</span><span>Hospital transport: {item.transported_to_hospital === null ? "not recorded" : item.transported_to_hospital ? "yes" : "no"}</span></td><td data-label="Source and confirmation"><span>{item.provenance.source_type.replaceAll("_", " ")}</span><span>{item.provenance.confirmation_state.replaceAll("_", " ")}</span></td></tr>)}</tbody></table></div>; }

export function EpisodesPage(): React.JSX.Element {
  const auth = useAuth();
  const profileTimezone = auth.session?.user.defaultTimezone ?? "UTC";
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => viewFromSearch(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const appliedDraft = useMemo(() => ({ ...filters, allHistory: view.allHistory }), [filters, view.allHistory]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters: appliedDraft });
  const draft = draftState.search === appliedSearch ? draftState.filters : appliedDraft;
  const setDraft = (next: RecordedHistoryFilters & HistoryDateRange): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const openEpisodes = useQuery({ queryKey: ["episodes", "open", filters, view.openPage, view.reviewEpisode], queryFn: () => getEpisodes(filters, view.openPage, "open", view.reviewEpisode ?? undefined), enabled: !invalidRange });
  const historyEpisodes = useQuery({ queryKey: ["episodes", "resolved", filters, view.historyPage], queryFn: () => getEpisodes(filters, view.historyPage, "resolved"), enabled: !invalidRange });
  const injections = useQuery({ queryKey: ["emergency-injections", filters, view.injectionPage], queryFn: () => getEmergencyInjections(filters, view.injectionPage), enabled: !invalidRange });
  const create = useMutation({ mutationFn: createEpisode, onSuccess: async () => { setSearchParams(searchFromView({ ...view, openPage: 1 })); await queryClient.invalidateQueries({ queryKey: ["episodes"] }); } });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    create.mutate({ trigger: data.get("trigger") as string, severity: optional(data.get("severity")) as Episode["severity"], time: { local_time: data.get("started_at") as string, timezone: data.get("timezone") as string, fold: null }, highest_temperature_c: optional(data.get("temperature")), illness_description: optional(data.get("illness")), notes: optional(data.get("notes")) }, { onSuccess: () => { form.reset(); } });
  }

  const open = openEpisodes.data?.items ?? [];
  const history = historyEpisodes.data?.items ?? [];
  return <Page title="Stress episodes" description="Group recorded symptoms, doses, and context for review without making causal claims.">
    <Alert color="orange" mb="xl"><strong>Recorded facts, not dosing instructions.</strong> A dose linked to an episode records what happened; it is not part of your physician-approved medication plan. Follow your approved emergency instructions and seek urgent care when appropriate.</Alert>
    <Paper component="section" withBorder p="lg" radius="lg" aria-labelledby="open-episode-heading">
      <Title order={2} id="open-episode-heading" mb="md">Open a new episode</Title>
      <form onSubmit={submit}><SimpleGrid className="episode-entry-form" cols={{ base: 1, sm: 2 }} spacing="md">
        <TextInput id="episode-trigger" label="Trigger" name="trigger" required maxLength={200} aria-label="Trigger" aria-describedby="episode-trigger-help" placeholder="For example: fever, injury, surgery, or major stress" description={<span id="episode-trigger-help">A short name for the event or circumstance that prompted you to track this episode—not a symptom or dosing instruction.</span>} />
        <NativeSelect id="episode-severity" label="Severity" name="severity" defaultValue="" aria-describedby="episode-severity-help" data={[{ value: "", label: "Not recorded" }, { value: "mild", label: "Mild" }, { value: "moderate", label: "Moderate" }, { value: "severe", label: "Severe" }]} description={<span id="episode-severity-help">Optional personal label for the episode&apos;s overall impact. It is not a clinical triage score.</span>} />
        <TextInput id="episode-start" label="Start time" name="started_at" type="datetime-local" required aria-label="Start time" defaultValue={nowLocal()} aria-describedby="episode-start-help" description={<span id="episode-start-help">When the episode began, using your best known local date and time.</span>} />
        <TextInput id="episode-timezone" label="IANA timezone" name="timezone" required aria-label="IANA timezone" defaultValue={profileTimezone} aria-describedby="episode-timezone-help" description={<span id="episode-timezone-help">Usually leave your profile timezone. Change it only if this local time occurred in another region, such as Europe/London.</span>} />
        <TextInput id="episode-temperature" label="Highest temperature (°C)" name="temperature" type="number" min="25" max="45" step="0.1" inputMode="decimal" aria-describedby="episode-temperature-help" description={<span id="episode-temperature-help">Optional highest measured temperature during this episode, entered in degrees Celsius.</span>} />
        <Textarea id="episode-context" label="Illness or stress context" name="illness" aria-describedby="episode-context-help" placeholder="Relevant circumstances, timing, or what was happening" description={<span id="episode-context-help">Optional details about the illness, injury, procedure, exertion, travel, or emotional stress surrounding the episode.</span>} />
        <Textarea id="episode-notes" label="Notes" name="notes" aria-describedby="episode-notes-help" placeholder="Other observations you want to remember" description={<span id="episode-notes-help">Optional extra context. Record doses and symptoms in their own forms so HealthCurve can link and graph them as separate facts.</span>} />
        <Group align="end"><Button loading={create.isPending} type="submit">Open episode</Button></Group>
      </SimpleGrid></form>
      {create.isError ? <Alert color="red" mt="md" role="alert">The episode could not be opened. Check the local time and IANA timezone.</Alert> : null}
    </Paper>
    <Paper component="form" withBorder p="lg" my="xl" radius="lg" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setSearchParams(searchFromView({ ...draft, openPage: 1, historyPage: 1, injectionPage: 1 })); }}>
      <Title order={2} mb="md">Filter episode history</Title>
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md"><TextInput label="From date" type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value, allHistory: false }); }} /><TextInput label="Through date" type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value, allHistory: false }); }} /><TextInput label="History IANA timezone" required aria-label="History IANA timezone" value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></SimpleGrid>
      {validation === null && !invalidRange ? null : <Alert color="red" mt="md" role="alert">{validation ?? "From date must be on or before Through date."}</Alert>}
      <Group mt="md"><Button type="submit">Apply history filters</Button><Button variant="outline" type="button" onClick={() => { const allHistory = { dateFrom: "", dateTo: "", timezone: profileTimezone, allHistory: true, openPage: 1, historyPage: 1, injectionPage: 1 }; setValidation(null); setDraftState({ search: "", filters: allHistory }); setSearchParams(searchFromView(allHistory)); }}>Clear history filters</Button></Group>
      <HistoryDateShortcuts dateFrom={draft.dateFrom} dateTo={draft.dateTo} timezone={draft.timezone} label="Quick episode dates" onSelect={(day) => { const selected = { ...view, ...draft, dateFrom: day, dateTo: day, allHistory: false, openPage: 1, historyPage: 1, injectionPage: 1 }; setValidation(null); setDraftState({ search: "", filters: selected }); setSearchParams(searchFromView(selected)); }} />
    </Paper>
    {view.allHistory ? <Alert color="blue"><strong>Showing all history.</strong> Choose dates or a quick date to bound episode and injection records again.</Alert> : null}
    <Text c="dimmed" my="md">Inclusive history dates use {timezoneAbbreviation(filters.timezone)} and filter by when each episode started or injection occurred.</Text>
    {openEpisodes.isFetching || historyEpisodes.isFetching || injections.isFetching ? <Text role="status">Loading episode and injection records…</Text> : null}{openEpisodes.isError || historyEpisodes.isError || injections.isError ? <Alert color="red" role="alert">Some episode or injection records could not be loaded.</Alert> : null}
    <section aria-labelledby="active-heading"><h2 id="active-heading">Open episodes</h2>{view.reviewEpisode == null ? null : <p className="privacy-note">Showing the episode selected from Data quality so you can confirm whether it is continuing or record its actual end time.</p>}{open.length === 0 && !openEpisodes.isFetching ? <p>{view.reviewEpisode == null ? "No open episodes match." : "The selected episode is no longer open or is not available."}</p> : <EpisodeTable episodes={open} label="Open episode records" reviewEpisode={view.reviewEpisode} />}{openEpisodes.data === undefined ? null : <PaginationControls label="Open episodes" metadata={openEpisodes.data.page} onPageChange={(openPage) => { setSearchParams(searchFromView({ ...view, openPage })); }} />}</section>
    <section aria-labelledby="history-heading"><h2 id="history-heading">Episode history</h2>{history.length === 0 && !historyEpisodes.isFetching ? <p>No resolved episodes match.</p> : <EpisodeTable episodes={history} label="Resolved episode records" />}{historyEpisodes.data === undefined ? null : <PaginationControls label="Episode history" metadata={historyEpisodes.data.page} onPageChange={(historyPage) => { setSearchParams(searchFromView({ ...view, historyPage })); }} />}</section>
    <section aria-labelledby="injection-heading"><h2 id="injection-heading">Emergency injection history</h2>{injections.data?.items.length === 0 ? <p>No emergency injections match.</p> : injections.data === undefined ? null : <InjectionTable injections={injections.data.items} />}{injections.data === undefined ? null : <PaginationControls label="Emergency injection records" metadata={injections.data.page} onPageChange={(injectionPage) => { setSearchParams(searchFromView({ ...view, injectionPage })); }} />}</section>
  </Page>;
}
