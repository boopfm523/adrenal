import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { recordTimezone } from "../api/client";
import { useAuth } from "../auth/context";
import { deviceTimezone, timezoneAbbreviation } from "../time";

const DISMISSED_KEY = "hc.timezone-prompt-dismissed";

function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(DISMISSED_KEY);
  } catch {
    // Private-mode storage failures must not cost the owner the banner.
    return null;
  }
}

function writeDismissed(zone: string): void {
  try {
    window.localStorage.setItem(DISMISSED_KEY, zone);
  } catch {
    // Dismissal then lasts for the page view only, which is the safe direction.
  }
}

/**
 * Offer to move the recorded zone when the device disagrees with it.
 *
 * Deliberately an offer. Switching on the device's say-so would silently reinterpret
 * every entry made afterwards -- and a laptop whose clock is simply set wrong would
 * then corrupt the curve without anyone being asked. The dismissal is remembered per
 * device zone, so declining in Denver does not also decline the next destination.
 */
export function TimezoneBanner(): React.JSX.Element | null {
  const { session } = useAuth();
  const queryClient = useQueryClient();
  const [dismissed, setDismissed] = useState<string | null>(readDismissed);
  const device = deviceTimezone();
  const recorded = session?.user.currentTimezone ?? null;

  const change = useMutation({
    mutationFn: (zone: string) => recordTimezone(zone),
    onSuccess: async () => {
      // Every list, curve, and day boundary was computed in the zone just left.
      await queryClient.invalidateQueries();
    },
  });

  if (session === null || device === null || recorded === null) return null;
  if (device === recorded) return null;
  if (dismissed === device) return null;

  const deviceLabel = `${device} (${timezoneAbbreviation(device)})`;
  const recordedLabel = `${recorded} (${timezoneAbbreviation(recorded)})`;

  return (
    <aside className="timezone-banner" role="region" aria-labelledby="timezone-banner-heading">
      <h2 id="timezone-banner-heading">This device is in a different timezone</h2>
      <p aria-live="polite">
        HealthCurve is recording in <strong>{recordedLabel}</strong>, but this device says
        you are in <strong>{deviceLabel}</strong>. Nothing already recorded moves either
        way; only new entries and the meaning of &ldquo;today&rdquo; change.
      </p>
      <div className="timezone-banner-actions">
        <button
          type="button"
          disabled={change.isPending}
          onClick={() => { change.mutate(device); }}
        >
          {change.isPending ? "Switching…" : `Switch to ${device}`}
        </button>
        <button
          className="button-secondary"
          type="button"
          onClick={() => { setDismissed(device); writeDismissed(device); }}
        >
          Keep {recorded}
        </button>
      </div>
      {change.isError ? (
        <p className="error-summary" role="alert">
          The timezone was not changed, so recording continues in {recorded}.
        </p>
      ) : null}
    </aside>
  );
}
