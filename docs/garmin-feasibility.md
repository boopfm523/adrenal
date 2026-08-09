# Garmin Integration Feasibility Finding

**Research date:** 2026-08-09
**Decision:** **No-go for direct Garmin Connect API implementation until Garmin approves
HealthCurve for the business/enterprise Developer Program and supplies the gated API
contract. Go for a local, owner-initiated FIT/CSV import fallback (`hc-p1u.1`).**

## Publicly confirmed capabilities

Garmin's current public Health API page confirms a cloud-to-cloud REST integration with
JSON health summaries after user consent and device synchronization. It advertises both
ping/pull and push delivery, selectable feeds, sample data, backfill tooling, and an
evaluation environment after approval.

Confirmed Health API categories are steps, intensity minutes, sleep, calories, heart
rate, stress, Pulse Ox, Body Battery, body composition, respiration, blood pressure,
and enhanced beat-to-beat interval data. The page specifically describes detailed
stress, Pulse Ox, epoch, and heart-rate data. The separate Activity API is required for
full recorded-activity data across activity types.

Sources:

- [Garmin Health API](https://developer.garmin.com/gc-developer-program/health-api/)
- [Garmin Connect Developer Program overview](https://developer.garmin.com/gc-developer-program/overview/)
- [Garmin Health SDK comparison](https://developer.garmin.com/health-sdk/)

### HealthCurve metric disposition

| Requested data | Public status | HealthCurve treatment |
|---|---|---|
| Sleep start/end and sleep data | Confirmed category | Map only fields present in the approved contract; never infer missing stages or score |
| Sleep score | Not explicitly guaranteed on the public page | Treat as unavailable until the gated schema confirms it |
| Heart rate | Confirmed | Preserve sample/summary granularity, timestamps, unit, device/source, and gaps |
| Intensity minutes | Confirmed | Preserve Garmin definition and interval; do not reinterpret as a clinical measure |
| Recorded activities | Confirmed through Activity API | Separate activity adapter/feed; preserve activity and provider IDs/revisions |
| HRV | Beat-to-beat/HRV-related data is advertised, but the precise Health API payload and entitlement are not public | Treat as unconfirmed until approved documentation names the summary/feed |
| Stress | Confirmed | Preserve Garmin value/definition; never present as diagnosis |
| Body Battery | Confirmed | Preserve as Garmin-derived metric with missingness; never compute a replacement |

Device capability varies. “Confirmed category” does not mean every device or account
produces it. Unsupported, unsynced, and zero are separate states.

## Access and commercial eligibility

The public Program FAQ says the Garmin Connect Developer Program is for business or
enterprise use. Garmin reviews applications and grants approved applicants access to
the Developer Portal and evaluation environment. The FAQ says there is no general
program licensing/maintenance fee, while some metrics or commercial uses may require a
license fee or minimum device order; the Health API page separately warns commercial
use requires a license fee. HealthCurve is presently a personal, single-owner project,
so approval cannot be assumed.

Garmin states that applications are normally reviewed within two business days and a
typical approved integration takes one to four weeks. These are vendor estimates, not
HealthCurve delivery commitments.

Source: [Garmin Connect Developer Program FAQ](https://developer.garmin.com/gc-developer-program/program-faq/)

## Authentication, consent, and token refresh

Garmin publicly confirms **OAuth 2.0** for all Developer Program APIs and requires
end-user consent before Health API data becomes accessible. Exact authorization URLs,
grant details, scopes, token lifetime, refresh-token rotation, revocation behavior, and
callback requirements are available only in the approved Developer Portal materials
and therefore cannot be responsibly implemented from the public site.

An approved integration must document and contract-test those details before code is
merged. HealthCurve must encrypt refresh/access tokens at rest, keep client credentials
outside the database and Git, bind callbacks to an anti-CSRF state value, support
disconnect/revocation, and never log tokens or authorization URLs containing secrets.

## Delivery, rate limits, and backfill

Publicly confirmed:

- Health data becomes available after the user syncs a supported device to Garmin
  Connect; this is not a real-time device stream.
- The Health API offers ping/pull or push architecture and customized subscriptions.
- Developer tools include sample data and user-data backfill.
- The approved development environment uses throttled access to production.

Not publicly specified as of the research date:

- Numeric request quotas or burst limits.
- Retry headers and vendor-specific backoff contract.
- Maximum backfill window, pagination/cursor contract, or late-revision window.
- Delivery retry duration, ordering, duplicate guarantees, and webhook verification.

These values must be copied from the approved contract into an integration ADR and
automated contract fixtures. Until then, the adapter design may assume only idempotent
provider IDs/revisions, bounded exponential backoff, durable cursors, replay tolerance,
and visible gaps—not any numeric limit or backfill promise.

## Retention, deletion, and attribution

The public marketing/FAQ pages do not state a complete Garmin Health API data-retention
or deletion contract. The applicable agreement supplied at approval must be reviewed
before persistence. At minimum HealthCurve will provide consent provenance, disconnect
and upstream revocation where supported, provider-scoped deletion, owner export, and a
record of import source/revision. Garmin data must not outlive a stricter contractual
limit.

Garmin's current API brand guidelines require Garmin device/source attribution on
dashboards, activity feeds, overview cards, and other primary displays using
device-sourced data; when the device model is not supplied, attribute Garmin as the
source. HealthCurve UI/report work must include this attribution without implying
Garmin endorsement.

Source: [Garmin API brand guidelines](https://developer.garmin.com/downloads/brand/Garmin-Developer-API-Brand-Guidelines.pdf)

## Evaluation environment

Garmin confirms that approved applicants receive an evaluation environment, developer
web tools, sample data, backfill tooling, and integration auto-verification before
production. No usable unauthenticated public sandbox is documented. Consequently,
HealthCurve cannot build or verify the direct adapter without program approval and
must not use scraped or reverse-engineered Garmin Connect endpoints.

## Recommendation

### Direct API: conditional no-go

Do not implement the cloud adapter yet. Apply only if Garmin confirms that a private,
single-owner HealthCurve deployment is an eligible business use and provides acceptable
terms. A later go decision requires evidence for:

1. Approved account and evaluation access.
2. Health and Activity API entitlements covering the required metrics.
3. OAuth/scopes/refresh/revocation details.
4. Numeric rate limits, push/pull verification, retry, ordering, and deduplication.
5. Backfill and provider-revision windows.
6. Retention, deletion, export, and attribution obligations.
7. Any license fee or minimum-device obligation explicitly accepted by the owner.
8. Synthetic evaluation fixtures and no credentials in the repository.

### Personal-use fallback: go

Garmin officially supports owner export of daily wellness FIT archives containing
steps, sleep, stress, HRV and other available wellness data, original FIT activity
files, activity CSV, reports, and a full account data export. Implement local reviewed
import under `hc-p1u.1`, using the official FIT SDK/profile rather than a private Garmin
web API. The workflow should be upload → parse locally → preview → confirm → persist,
with checksums, device/source attribution, idempotency, and explicit missingness.

Sources:

- [Garmin Support: exporting Garmin Connect data](https://support.garmin.com/en-ZA/?faq=W1TvTPW8JZ6LfJSfK512Q8)
- [Garmin FIT Activity file specification](https://developer.garmin.com/fit/file-types/activity/)

The fallback is manual rather than continuously synchronized, but it meets the
personal-use privacy boundary, requires no vendor credential, and remains valid even if
Developer Program access is denied.
