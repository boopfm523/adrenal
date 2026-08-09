import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createEpisode, getEpisodes, updateEpisode, type Episode } from "../api/client";
import { useAuth } from "../auth/context";
import { FactCard } from "../components/CategoryCards";
import { Page } from "../components/Page";

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

function EpisodeCard({ episode }: { episode: Episode }): React.JSX.Element {
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

  return <FactCard title={episode.trigger} metadata={<span>{episode.dose_count} linked dose{episode.dose_count === 1 ? "" : "s"} · {episode.symptom_count} linked symptom{episode.symptom_count === 1 ? "" : "s"}</span>}>
    <p className={`episode-status episode-status--${episode.status}`}>{episode.status}</p>
    <dl className="provenance-grid"><div><dt>Started</dt><dd>{displayTime(episode.started_at, episode.timezone)}</dd></div><div><dt>Timezone</dt><dd>{episode.timezone}</dd></div><div><dt>Severity</dt><dd>{episode.severity ?? "Not recorded"}</dd></div><div><dt>Ended</dt><dd>{episode.ended_at === null ? "Episode remains open" : displayTime(episode.ended_at, episode.timezone)}</dd></div></dl>
    {episode.illness_description === null ? null : <p><strong>Context:</strong> {episode.illness_description}</p>}
    {episode.highest_temperature_c === null ? null : <p><strong>Highest recorded temperature:</strong> {episode.highest_temperature_c} °C</p>}
    {episode.notes === null ? null : <p><strong>Notes:</strong> {episode.notes}</p>}
    {episode.recovery_notes === null ? null : <p><strong>Recovery notes:</strong> {episode.recovery_notes}</p>}
    {episode.outcome === null ? null : <p><strong>Outcome:</strong> {episode.outcome}</p>}
    {episode.status !== "open" ? null : <><button className="button-secondary" type="button" onClick={() => { setEditing(!editing); }}>{editing ? "Cancel changes" : "Add or update context"}</button>
      {editing ? <form className="correction-form" onSubmit={submit}><label>Severity<select name="severity" defaultValue={episode.severity ?? ""}><option value="">Not recorded</option><option value="mild">Mild</option><option value="moderate">Moderate</option><option value="severe">Severe</option></select></label><label>Highest temperature (°C)<input name="temperature" type="number" min="25" max="45" step="0.1" defaultValue={episode.highest_temperature_c ?? ""} /></label><label className="form-wide">Illness or stress context<textarea name="illness" defaultValue={episode.illness_description ?? ""} /></label><label className="form-wide">Notes<textarea name="notes" defaultValue={episode.notes ?? ""} /></label><label className="form-wide">Recovery notes<textarea name="recovery" defaultValue={episode.recovery_notes ?? ""} /></label><label className="form-wide">Outcome<textarea name="outcome" defaultValue={episode.outcome ?? ""} /></label><button disabled={mutation.isPending} type="submit">Save context</button></form> : null}
      <form className="episode-close-form" onSubmit={close}><label>Episode end time<input name="ended_at" type="datetime-local" required defaultValue={nowLocal()} /></label><button disabled={mutation.isPending} type="submit">Close episode</button></form></>}
    {mutation.isError ? <p className="error-summary" role="alert">The episode could not be updated. Check the time and try again.</p> : null}
  </FactCard>;
}

export function EpisodesPage(): React.JSX.Element {
  const auth = useAuth();
  const timezone = auth.session?.user.defaultTimezone ?? "UTC";
  const queryClient = useQueryClient();
  const episodes = useQuery({ queryKey: ["episodes"], queryFn: getEpisodes });
  const create = useMutation({ mutationFn: createEpisode, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["episodes"] }); } });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    create.mutate({ trigger: data.get("trigger") as string, severity: optional(data.get("severity")) as Episode["severity"], time: { local_time: data.get("started_at") as string, timezone: data.get("timezone") as string, fold: null }, highest_temperature_c: optional(data.get("temperature")), illness_description: optional(data.get("illness")), notes: optional(data.get("notes")) }, { onSuccess: () => { form.reset(); } });
  }

  const open = episodes.data?.filter((episode) => episode.status === "open") ?? [];
  const history = episodes.data?.filter((episode) => episode.status !== "open") ?? [];
  return <Page title="Stress episodes" description="Group recorded symptoms, doses, and context for review without making causal claims.">
    <aside className="safety-note"><strong>Recorded facts, not dosing instructions.</strong> A dose linked to an episode records what happened; it is not part of your physician-approved medication plan. Follow your approved emergency instructions and seek urgent care when appropriate.</aside>
    <section aria-labelledby="open-episode-heading"><h2 id="open-episode-heading">Open a new episode</h2><form className="correction-form" onSubmit={submit}><label>Trigger<input name="trigger" required maxLength={200} /></label><label>Severity<select name="severity" defaultValue=""><option value="">Not recorded</option><option value="mild">Mild</option><option value="moderate">Moderate</option><option value="severe">Severe</option></select></label><label>Start time<input name="started_at" type="datetime-local" required defaultValue={nowLocal()} /></label><label>IANA timezone<input name="timezone" required defaultValue={timezone} /></label><label>Highest temperature (°C)<input name="temperature" type="number" min="25" max="45" step="0.1" /></label><label className="form-wide">Illness or stress context<textarea name="illness" /></label><label className="form-wide">Notes<textarea name="notes" /></label><button disabled={create.isPending} type="submit">Open episode</button></form>{create.isError ? <p className="error-summary" role="alert">The episode could not be opened. Check the local time and IANA timezone.</p> : null}</section>
    {episodes.isPending ? <p role="status">Loading episodes…</p> : null}{episodes.isError ? <p className="error-summary" role="alert">Episodes could not be loaded.</p> : null}
    <section aria-labelledby="active-heading"><h2 id="active-heading">Open episodes</h2>{open.length === 0 && !episodes.isPending ? <p>No episodes are currently open.</p> : open.map((episode) => <EpisodeCard key={episode.id} episode={episode} />)}</section>
    <section aria-labelledby="history-heading"><h2 id="history-heading">Episode history</h2>{history.length === 0 && !episodes.isPending ? <p>No closed episodes recorded.</p> : history.map((episode) => <EpisodeCard key={episode.id} episode={episode} />)}</section>
  </Page>;
}
