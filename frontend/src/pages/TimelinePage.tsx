import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getTimeline, type TimelineFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { ContextCard, FactCard } from "../components/CategoryCards";
import { Page } from "../components/Page";

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
] as const;

function localTime(value: string): string {
  return value.replace("T", " ").slice(0, 16);
}

export function TimelinePage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const emptyFilters: TimelineFilters = { type: "", dateFrom: "", dateTo: "", timezone, includeSensitive: false };
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState(emptyFilters);
  const timeline = useQuery({ queryKey: ["timeline", filters], queryFn: () => getTimeline(filters) });
  const filtered = filters.type !== "" || filters.dateFrom !== "" || filters.dateTo !== "" || filters.includeSensitive;

  return (
    <Page title="Timeline" description="The authoritative chronology of recorded facts, with source and correction provenance.">
      <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); setFilters(draft); }}>
        <label>Record type
          <select value={draft.type} onChange={(event) => { setDraft({ ...draft, type: event.target.value }); }}>
            {eventTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>From date
          <input type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} />
        </label>
        <label>Through date
          <input type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} />
        </label>
        <label className="checkbox-label">
          <input type="checkbox" checked={draft.includeSensitive} onChange={(event) => { setDraft({ ...draft, includeSensitive: event.target.checked }); }} />
          Include sensitive diary entries
        </label>
        <div className="filter-actions">
          <button type="submit">Apply filters</button>
          <button className="button-secondary" type="button" onClick={() => { setDraft(emptyFilters); setFilters(emptyFilters); }}>Clear filters</button>
        </div>
      </form>
      <p className="privacy-note">Sensitive diary entries are hidden by default. Dates use {timezone}.</p>

      {timeline.isPending ? <p role="status">Loading timeline…</p> : null}
      {timeline.isError ? <p className="error-summary" role="alert">The timeline could not be loaded.</p> : null}
      {timeline.data?.items.length === 0 ? (
        <section className="empty-state">
          <h2>{filtered ? "No records match these filters" : "No records yet"}</h2>
          <p>{filtered ? "Change or clear the filters to see other recorded facts." : "New facts will appear here after they are recorded."}</p>
        </section>
      ) : null}
      <div className="timeline-list">
        {timeline.data?.items.map((item) => {
          const Card = item.event_type === "context" ? ContextCard : FactCard;
          return (
          <Card key={item.id} title={item.summary} metadata={
            <span>{item.provenance?.is_correction ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span>
          }>
            <dl className="provenance-grid">
              <div><dt>Type</dt><dd>{item.event_type.replace("_", " ")}</dd></div>
              <div><dt>Experienced time</dt><dd>{localTime(item.time.local_time)} · {item.time.timezone}</dd></div>
              <div><dt>Source</dt><dd>{item.provenance?.source_type.replace("_", " ") ?? "Not available"}</dd></div>
              <div><dt>Confirmation</dt><dd>{item.provenance?.confirmation_state.replace("_", " ") ?? "Not available"}</dd></div>
            </dl>
          </Card>
          );
        })}
      </div>
    </Page>
  );
}
