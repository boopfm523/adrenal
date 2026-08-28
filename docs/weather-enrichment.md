# Open-Meteo weather enrichment

**Owner approval:** Open-Meteo and the disclosure below were approved on 2026-08-10
and are recorded without secrets in Beads issue `hc-p1u.2.3`.

When the owner deliberately shares a phone location with the Telegram bot and then
confirms the associated draft, HealthCurve stores only coordinates rounded to one
decimal degree. A transactional background job sends Open-Meteo only:

- the already-rounded latitude and longitude;
- a fixed list of current-weather field names;
- `timezone=auto`; and
- a request for Unix timestamps.

It does not send Telegram text, health facts, an owner/draft/event identifier, or exact
coordinates. Open-Meteo's personal/noncommercial API does not require a credential, so
HealthCurve stores no Open-Meteo secret. Any future paid-plan key must use the existing
encrypted integration-credential store and requires a new owner-approved data-flow
review.

The provider may log IP address, request coordinates, and request metadata under its
published policy at <https://open-meteo.com/en/terms>. The owner approved that
provider-side behavior for this data flow. HealthCurve does not send the source event
time; Open-Meteo returns the observation time for its current-weather response.

Weather is stored as a separate provider-imported context fact with provider, source,
observation time, units, and an opaque observation identifier. Open-Meteo supplies no
confidence value, so confidence remains missing rather than being invented. A missing
measurement remains `null`, never zero. Network errors, timeouts, HTTP 429, and server
errors receive bounded retries and then become a visible dead-letter job.

The Settings & privacy page can delete Open-Meteo-derived rows independently. This does
not delete the underlying coarse location or any health record.

## Garmin outdoor activity intervals

The owner separately approved private historical weather for supported Garmin outdoor
walking and running activities on 2026-08-28. HealthCurve uses Garmin's start
coordinates directly after rounding both values to 0.1 degrees; it does not reverse
geocode them, retain route geometry, send an activity title, or put coordinates in a
job payload. Garmin's private `locationName` is retained only as the display label.

Explicit indoor/treadmill walking or running and rowing-machine activities are
classified as indoor before the job is queued and never call Open-Meteo. Missing or
invalid coordinates remain missing. Eligible jobs request only fixed historical
hourly temperature, apparent temperature, humidity, precipitation, weather-code,
wind-speed, and wind-gust fields for the UTC dates covering the activity interval.
The resulting interval summary is a separate provider context fact with provenance.

Activity location and weather are available to the authenticated curve, timeline,
and private chat/cross-event analysis. They remain excluded from the public static
site's allow-list under [ADR-0033](adr/0033-private-garmin-activity-weather.md).
