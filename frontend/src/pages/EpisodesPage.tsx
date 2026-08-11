import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { createEpisode, getEmergencyInjections, getEpisodes, updateEpisode, type Episode, type Injection, type RecordedHistoryFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatDecimal, formatMeasurement } from "../format";
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

interface EpisodeViewState extends RecordedHistoryFilters { openPage: number; historyPage: number; injectionPage: number }

function pageNumber(params: URLSearchParams, name: string): number { const value = params.get(name) ?? ""; return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : 1; }
function viewFromSearch(search: string, profileTimezone: string): EpisodeViewState { const params = new URLSearchParams(search); return { dateFrom: params.get("local_date_from") ?? "", dateTo: params.get("local_date_to") ?? "", timezone: params.get("timezone") ?? profileTimezone, openPage: pageNumber(params, "open_page"), historyPage: pageNumber(params, "history_page"), injectionPage: pageNumber(params, "injection_page") }; }
function searchFromView(view: EpisodeViewState): URLSearchParams { const params = new URLSearchParams({ timezone: view.timezone }); if (view.dateFrom !== "") params.set("local_date_from", view.dateFrom); if (view.dateTo !== "") params.set("local_date_to", view.dateTo); if (view.openPage > 1) params.set("open_page", view.openPage.toString()); if (view.historyPage > 1) params.set("history_page", view.historyPage.toString()); if (view.injectionPage > 1) params.set("injection_page", view.injectionPage.toString()); return params; }

function EpisodeRow({ episode }: { episode: Episode }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
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

  return <><tr><td className="timeline-time"><time dateTime={episode.started_at}>{displayTime(episode.started_at, episode.timezone)}</time><span>{timezoneAbbreviation(episode.timezone, episode.started_at)}</span></td><th scope="row">{episode.trigger}</th><td><span className={`episode-status episode-status--${episode.status}`}>{episode.status}</span><span>Severity: {episode.severity ?? "not recorded"}</span><span>{episode.ended_at === null ? "Episode remains open" : `Ended ${displayTime(episode.ended_at, episode.timezone)}`}</span></td><td><span>{formatDecimal(episode.dose_count)} linked dose{episode.dose_count === 1 ? "" : "s"}</span><span>{formatDecimal(episode.symptom_count)} linked symptom{episode.symptom_count === 1 ? "" : "s"}</span></td><td>{episode.illness_description === null ? null : <span>Context: {episode.illness_description}</span>}{episode.highest_temperature_c === null ? null : <span>Highest temperature: {formatDecimal(episode.highest_temperature_c)} °C</span>}{episode.notes === null ? null : <span>Notes: {episode.notes}</span>}{episode.recovery_notes === null ? null : <span>Recovery: {episode.recovery_notes}</span>}{episode.outcome === null ? null : <span>Outcome: {episode.outcome}</span>}{episode.status !== "open" ? null : <button className="button-secondary" type="button" onClick={() => { setEditing(!editing); }}>{editing ? "Cancel changes" : "Add or update context"}</button>}</td></tr>
    {episode.status !== "open" ? null : <tr className="correction-table-row"><td colSpan={5}>{!editing ? null : <form className="correction-form" onSubmit={submit}><label>Severity<select name="severity" defaultValue={episode.severity ?? ""}><option value="">Not recorded</option><option value="mild">Mild</option><option value="moderate">Moderate</option><option value="severe">Severe</option></select></label><label>Highest temperature (°C)<input name="temperature" type="number" min="25" max="45" step="0.1" defaultValue={episode.highest_temperature_c ?? ""} /></label><label className="form-wide">Illness or stress context<textarea name="illness" defaultValue={episode.illness_description ?? ""} /></label><label className="form-wide">Notes<textarea name="notes" defaultValue={episode.notes ?? ""} /></label><label className="form-wide">Recovery notes<textarea name="recovery" defaultValue={episode.recovery_notes ?? ""} /></label><label className="form-wide">Outcome<textarea name="outcome" defaultValue={episode.outcome ?? ""} /></label><button disabled={mutation.isPending} type="submit">Save context</button></form>}<form className="episode-close-form" onSubmit={close}><label>Episode end time<input name="ended_at" type="datetime-local" required defaultValue={nowLocal()} /></label><button disabled={mutation.isPending} type="submit">Close episode</button></form>{mutation.isError ? <p className="error-summary" role="alert">The episode could not be updated. Check the time and try again.</p> : null}</td></tr>}
  </>;
}

function EpisodeTable({ episodes, label }: { episodes: Episode[]; label: string }): React.JSX.Element { return <div className="table-scroll" tabIndex={0} role="region" aria-label={`${label} table`}><table><caption>{label}, ordered by episode start time, latest first.</caption><thead><tr><th scope="col">Started</th><th scope="col">Trigger</th><th scope="col">Status and duration</th><th scope="col">Linked recorded facts</th><th scope="col">Recorded context and actions</th></tr></thead><tbody>{episodes.map((episode) => <EpisodeRow key={episode.id} episode={episode} />)}</tbody></table></div>; }

function InjectionTable({ injections }: { injections: Injection[] }): React.JSX.Element { return <div className="table-scroll" tabIndex={0} role="region" aria-label="Emergency injection records table"><table><caption>Recorded emergency injection facts, latest experienced time first.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Amount and route</th><th scope="col">Reason and response</th><th scope="col">Emergency follow-up</th><th scope="col">Source and confirmation</th></tr></thead><tbody>{injections.map((item) => <tr key={item.id}><td className="timeline-time">{item.time.local_time.replace("T", " ").slice(0, 16)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th scope="row">{formatMeasurement(item.amount, item.unit)}<span>{item.route.replaceAll("_", " ")}</span></th><td><span>{item.reason ?? "Reason not recorded"}</span><span>{item.response ?? "Response not recorded"}</span></td><td><span>Emergency services: {item.emergency_services_called === null ? "not recorded" : item.emergency_services_called ? "called" : "not called"}</span><span>Hospital transport: {item.transported_to_hospital === null ? "not recorded" : item.transported_to_hospital ? "yes" : "no"}</span></td><td><span>{item.provenance.source_type.replaceAll("_", " ")}</span><span>{item.provenance.confirmation_state.replaceAll("_", " ")}</span></td></tr>)}</tbody></table></div>; }

export function EpisodesPage(): React.JSX.Element {
  const auth = useAuth();
  const profileTimezone = auth.session?.user.defaultTimezone ?? "UTC";
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => viewFromSearch(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: RecordedHistoryFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const openEpisodes = useQuery({ queryKey: ["episodes", "open", filters, view.openPage], queryFn: () => getEpisodes(filters, view.openPage, "open"), enabled: !invalidRange });
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
    <aside className="safety-note"><strong>Recorded facts, not dosing instructions.</strong> A dose linked to an episode records what happened; it is not part of your physician-approved medication plan. Follow your approved emergency instructions and seek urgent care when appropriate.</aside>
    <section aria-labelledby="open-episode-heading"><h2 id="open-episode-heading">Open a new episode</h2><form className="correction-form" onSubmit={submit}><label>Trigger<input name="trigger" required maxLength={200} /></label><label>Severity<select name="severity" defaultValue=""><option value="">Not recorded</option><option value="mild">Mild</option><option value="moderate">Moderate</option><option value="severe">Severe</option></select></label><label>Start time<input name="started_at" type="datetime-local" required defaultValue={nowLocal()} /></label><label>IANA timezone<input name="timezone" required defaultValue={profileTimezone} /></label><label>Highest temperature (°C)<input name="temperature" type="number" min="25" max="45" step="0.1" /></label><label className="form-wide">Illness or stress context<textarea name="illness" /></label><label className="form-wide">Notes<textarea name="notes" /></label><button disabled={create.isPending} type="submit">Open episode</button></form>{create.isError ? <p className="error-summary" role="alert">The episode could not be opened. Check the local time and IANA timezone.</p> : null}</section>
    <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setSearchParams(searchFromView({ ...draft, openPage: 1, historyPage: 1, injectionPage: 1 })); }}><label>From date<input type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label><label>Through date<input type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label><label>History IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label>{validation === null && !invalidRange ? null : <p className="error-summary form-wide" role="alert">{validation ?? "From date must be on or before Through date."}</p>}<div className="filter-actions"><button type="submit">Apply history filters</button><button className="button-secondary" type="button" onClick={() => { setValidation(null); setDraftState({ search: "", filters: { dateFrom: "", dateTo: "", timezone: profileTimezone } }); setSearchParams(new URLSearchParams()); }}>Clear history filters</button></div></form>
    <p className="privacy-note">Inclusive history dates use {timezoneAbbreviation(filters.timezone)} and filter by when each episode started or injection occurred.</p>
    {openEpisodes.isFetching || historyEpisodes.isFetching || injections.isFetching ? <p role="status">Loading episode and injection records…</p> : null}{openEpisodes.isError || historyEpisodes.isError || injections.isError ? <p className="error-summary" role="alert">Some episode or injection records could not be loaded.</p> : null}
    <section aria-labelledby="active-heading"><h2 id="active-heading">Open episodes</h2>{open.length === 0 && !openEpisodes.isFetching ? <p>No open episodes match.</p> : <EpisodeTable episodes={open} label="Open episode records" />}{openEpisodes.data === undefined ? null : <PaginationControls label="Open episodes" metadata={openEpisodes.data.page} onPageChange={(openPage) => { setSearchParams(searchFromView({ ...view, openPage })); }} />}</section>
    <section aria-labelledby="history-heading"><h2 id="history-heading">Episode history</h2>{history.length === 0 && !historyEpisodes.isFetching ? <p>No resolved episodes match.</p> : <EpisodeTable episodes={history} label="Resolved episode records" />}{historyEpisodes.data === undefined ? null : <PaginationControls label="Episode history" metadata={historyEpisodes.data.page} onPageChange={(historyPage) => { setSearchParams(searchFromView({ ...view, historyPage })); }} />}</section>
    <section aria-labelledby="injection-heading"><h2 id="injection-heading">Emergency injection history</h2>{injections.data?.items.length === 0 ? <p>No emergency injections match.</p> : injections.data === undefined ? null : <InjectionTable injections={injections.data.items} />}{injections.data === undefined ? null : <PaginationControls label="Emergency injection records" metadata={injections.data.page} onPageChange={(injectionPage) => { setSearchParams(searchFromView({ ...view, injectionPage })); }} />}</section>
  </Page>;
}
