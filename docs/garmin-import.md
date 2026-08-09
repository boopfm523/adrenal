# Importing Garmin exports

HealthCurve can locally parse owner-exported Garmin data without a Garmin developer
account or a third-party upload. The workflow is deliberately two-step:

1. `POST /api/v1/integrations/garmin/imports/preview` parses the file and returns
   candidates, warnings, observed metrics, and missing metrics. It creates no facts.
2. Review that response, then send the same file and its returned `source_sha256` to
   `POST /api/v1/integrations/garmin/imports/confirm`. Only this request persists the
   source and confirmed facts.

Both routes require login and the session's `X-CSRF-Token`. The optional `timezone`
form field defaults to the owner's timezone. Confirmation reparses the upload and
rejects it if the checksum differs from the preview.

## Supported inputs

- Individual FIT files, decoded with the pinned official Garmin FIT SDK/profile.
- Garmin Connect-style activity CSV with a date and activity type/sport.
- ZIP account exports containing FIT, CSV, or one nested ZIP.

The import recognizes only explicit source fields:

| HealthCurve fact | FIT/CSV source |
|---|---|
| Heart rate | FIT record, monitoring, or HSA heart-rate fields |
| Resting heart rate | FIT monitoring HR data |
| HRV | FIT HRV value/status or session RMSSD/SDRR |
| Stress | FIT stress, HSA stress, or session average stress |
| Body Battery | FIT HSA Body Battery level |
| Steps | FIT HSA step data |
| Intensity minutes | FIT monitoring moderate/vigorous minutes |
| Sleep and score | Explicit FIT sleep-level bounds and optional assessment score |
| Activity | FIT session, or activity CSV with explicit columns |

A metric not present in the file remains missing. HealthCurve never creates a zero,
estimates sleep bounds, assumes a distance unit, or manufactures an unavailable score.
CSV distance is imported only when the header explicitly says metres, kilometres, or
miles. Device capabilities differ, so a supported metric can still be absent.

## Limits and failure behavior

Uploads are limited to 25 MiB, 500 total archive members, 100 MiB total expanded data,
a 100:1 compression ratio, two archive levels, 50,000 CSV rows, and 100,000 candidates.
Archive paths must be relative and safe. Encrypted, malformed, CRC-invalid, future-
dated, oversized, or unsupported inputs fail with a short reason code and create no
facts. Parsing does not write temporary files.

## Provenance and privacy

On confirmation, HealthCurve retains the exact uploaded bytes, whole-file and member
SHA-256 checksums, source member names, FIT profile version, observed/missing metrics,
and Garmin/device attribution. A raw serial is not copied into queryable columns; a
one-way hash provides stable device attribution. The exact owner export is retained for
reproducibility and may itself contain a serial or other sensitive metadata.
Reconfirming the same owner/checksum returns the existing batch and does not duplicate
facts.

Garmin-derived views and reports must identify Garmin or the supplied device model as
the source. This is a manual import, not continuous synchronization, and does not use
Garmin credentials, scrape Garmin Connect, or imply Garmin endorsement. Exported files
contain sensitive health and location-adjacent timestamps; handle them like the rest of
the HealthCurve record and ensure encrypted backups are configured.

Only synthetic FIT bytes generated at test runtime are used in the repository. Never
commit an actual Garmin export.
