import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getTimeline, type TimelineFilters } from "../api/client";
import { useAuth } from "../auth/context";
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

export function TimelinePage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const emptyFilters: TimelineFilters = { type: "", dateFrom: "", dateTo: "", timezone, includeSensitive: false, sortOrder: "asc" };
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
        <label>Order
          <select value={draft.sortOrder} onChange={(event) => { setDraft({ ...draft, sortOrder: event.target.value as TimelineFilters["sortOrder"] }); }}>
            <option value="asc">Earliest first</option>
            <option value="desc">Latest first</option>
          </select>
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
      {timeline.data === undefined || timeline.data.items.length === 0 ? null : (
        <div className="table-scroll timeline-table-region" tabIndex={0} role="region" aria-label="Timeline records table">
          <table className="timeline-table">
            <caption>Timeline records ordered by experienced time, {filters.sortOrder === "asc" ? "earliest first" : "latest first"}.</caption>
            <thead><tr><th scope="col">Experienced time</th><th scope="col">Record</th><th scope="col">Source and status</th><th scope="col">Provenance</th></tr></thead>
            <tbody>{timeline.data.items.map((item) => {
              const note = categoryNote(item.category, item.event_type);
              return <tr key={item.id} data-category={item.event_type === "context" ? "context" : item.category}>
                <td className="timeline-time"><time dateTime={item.time.occurred_at}>{localTime(item.time.local_time)}</time><span>{item.time.timezone}</span></td>
                <th scope="row"><span className="timeline-summary">{item.summary}</span><span className="timeline-type">{words(item.event_type)}</span></th>
                <td><span>{item.provenance?.source_type === undefined ? "Source not available" : words(item.provenance.source_type)}</span><span>{item.provenance?.confirmation_state === undefined ? "Confirmation not available" : words(item.provenance.confirmation_state)}</span></td>
                <td><span className={`timeline-category timeline-category--${item.event_type === "context" ? "context" : item.category}`}>{categoryLabel(item.category, item.event_type)}</span><span>{item.provenance?.is_correction === true ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span>{note === null ? null : <span className="timeline-category-note">{note}</span>}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
