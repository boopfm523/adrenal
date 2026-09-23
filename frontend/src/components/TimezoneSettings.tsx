import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, getTimezoneSettings, recordTimezone, type TimezoneStay } from "../api/client";
import { formatUnzonedDateTime } from "../time";

const SOURCE_LABELS: Record<TimezoneStay["source"], string> = {
  telegram: "Telegram",
  web: "This web app",
  cli: "Command line",
};

function stayRow(stay: TimezoneStay): React.JSX.Element {
  return (
    <tr key={stay.id}>
      <td className="timeline-time">
        {formatUnzonedDateTime(stay.started_at_local)}
        <span>{stay.abbreviation}</span>
      </td>
      <th scope="row">{stay.timezone}</th>
      <td>{SOURCE_LABELS[stay.source]}</td>
      <td>{stay.label ?? "No label"}</td>
    </tr>
  );
}

/**
 * Where HealthCurve thinks you are, and how to correct it.
 *
 * Home and current are shown side by side on purpose: they are different facts, and
 * conflating them is what made a dose taken abroad read back at the wrong hour.
 */
export function TimezoneSettings(): React.JSX.Element {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["timezone-settings"], queryFn: getTimezoneSettings });
  const change = useMutation({
    mutationFn: ({ timezone, label }: { timezone: string; label: string }) =>
      recordTimezone(timezone, label),
    onSuccess: async () => {
      // Every day boundary on screen was computed in the zone just left.
      await queryClient.invalidateQueries();
    },
  });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const timezone = (data.get("timezone") as string | null ?? "").trim();
    if (timezone === "") return;
    change.mutate(
      { timezone, label: (data.get("label") as string | null ?? "").trim() },
      { onSuccess: () => { form.reset(); } },
    );
  }

  const current = settings.data;

  return (
    <section aria-labelledby="timezone-heading">
      <h2 id="timezone-heading">Timezone while travelling</h2>
      <div className="settings-card">
        {settings.isPending ? <p role="status">Loading your timezone…</p> : null}
        {settings.isError ? (
          <p className="error-summary" role="alert">Your timezone could not be loaded.</p>
        ) : null}
        {current === undefined ? null : (
          <>
            <p>
              <strong>Recording in:</strong> {current.current_timezone} ({current.current_abbreviation}),
              local time {current.current_local_time.slice(11, 16)}
            </p>
            <p><strong>Home timezone:</strong> {current.home_timezone}</p>
            <p className="privacy-note">
              {current.is_home
                ? "You are recorded as being at home."
                : "You are recorded as away from home. New entries use the zone above; nothing already recorded has moved."}
            </p>
          </>
        )}
        <form className="filter-panel" onSubmit={submit}>
          <label>
            Where are you now?
            <input
              name="timezone"
              required
              maxLength={120}
              placeholder="Denver, or America/Denver"
              autoComplete="off"
            />
          </label>
          <label>Optional label<input name="label" maxLength={120} placeholder="Denver trip" /></label>
          <div className="filter-actions">
            <button type="submit" disabled={change.isPending}>
              {change.isPending ? "Recording…" : "Record this timezone"}
            </button>
          </div>
          {change.isSuccess ? (
            <p className="success-message" role="status">
              {change.data.recorded
                ? `Recording in ${change.data.current_timezone} from now on.`
                : `You were already recorded as being in ${change.data.current_timezone}. Nothing changed.`}
            </p>
          ) : null}
          {change.isError ? (
            <p className="error-summary" role="alert">
              {change.error instanceof ApiError && change.error.code === "ambiguous_place"
                ? "That place matches more than one timezone. Name the IANA zone directly, such as America/Denver."
                : "That is not a timezone or a place HealthCurve knows. Nothing changed; try a larger city nearby, or the IANA zone directly."}
            </p>
          ) : null}
        </form>
        <h3>Recent stays</h3>
        {current?.stays.length === 0 ? (
          <p className="empty-state">No travel has been recorded. Times use your home timezone.</p>
        ) : null}
        {current === undefined || current.stays.length === 0 ? null : (
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Recorded timezone stays table">
            <table>
              <caption>Recorded timezone stays, most recent first. Each one lasts until the next begins.</caption>
              <thead>
                <tr>
                  <th scope="col">Began (local time there)</th>
                  <th scope="col">Timezone</th>
                  <th scope="col">Recorded from</th>
                  <th scope="col">Label</th>
                </tr>
              </thead>
              <tbody>{current.stays.map(stayRow)}</tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
