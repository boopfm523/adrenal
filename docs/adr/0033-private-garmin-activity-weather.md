# ADR-0033: Private Garmin activity location and historical weather

**Status:** Accepted — 2026-08-28

## Context

Garmin's activity-list response can include a private display location and start/end
coordinates. Outdoor heat, cold, humidity, precipitation, and wind can be useful
context when reviewing walking or running, but route geometry and exact coordinates
are unnecessary. ADR-0032 deliberately excludes activity location and weather from
the public static site.

## Decision

For Garmin walking and running activities, HealthCurve classifies explicit indoor and
treadmill types as indoor before weather work is queued. Rowing and rowing-machine
types are also indoor for this purpose. Only supported outdoor walking/running with a
valid coordinate pair is eligible.

HealthCurve stores the provider's location label and the start coordinate rounded to
0.1 degrees. It does not store end coordinates, route points, or title-derived
locations. A durable job contains only the opaque activity fact ID. The worker sends
Open-Meteo the rounded coordinates, UTC activity dates, and a fixed allow-list of
historical hourly fields. It deterministically summarizes hourly observations that
cover the activity interval and records them as a separate provider context fact
logically linked by the activity fact ID in its provider provenance.

The private curve, timeline, and chat may display or compare this activity context.
The public static exporter continues to allow-list only the generic activity type,
times, duration, optional distance, and generic provenance from ADR-0032. It never
exports the location label, coordinates, environment classification, or weather.

## Consequences

- Outdoor activity review can include actual temperature, maximum apparent
  temperature, humidity, precipitation, conditions, wind, and provider provenance.
- Missing/invalid coordinates remain missing and never trigger a geocoder or weather
  call.
- Indoor/treadmill/rowing activities cannot disclose coordinates to Open-Meteo.
- Deleting Garmin data removes its linked activity-weather context; deleting weather
  context never deletes a health fact.
- The temperature words shown in the private curve are descriptive display bands,
  not alerts, diagnoses, exercise-safety conclusions, or medication guidance.

## Alternatives considered

**Reverse geocode the location name.** Rejected because Garmin supplies coordinates
and an additional provider disclosure is unnecessary.

**Store full routes.** Rejected because route detail is unnecessary for weather and
would materially increase location sensitivity.

**Publish activity weather.** Rejected because the owner authorized it for private
analysis, not the public static site.
