# HealthCurve UI Information Architecture

**Status:** Phase 0 implementation guide
**Scope:** navigation, page purpose, primary actions, state treatment, category labeling,
and accessibility baseline.
**Excludes:** visual design system, component implementation, and the unresolved access
policy for the emergency page (`hc-h0e`).

This document turns the page inventory in the project plan into a testable low-fidelity
map. It follows ADR-0005: the primary application is a React SPA, while `/emergency`
is a server-rendered, JavaScript-independent page.

## 1. Navigation model

### Primary navigation

The authenticated SPA uses the following stable order on desktop and in the mobile
navigation drawer:

1. **Today** — daily status and fastest ordinary capture.
2. **Timeline** — the canonical chronological record.
3. **Plan & doses** — physician-approved regimen and actual-versus-plan views.
4. **Episodes** — stress/up-dose and emergency-injection episode review.
5. **Symptoms & Meals** — subjective symptoms, meals, diary notes, and contextual life events.
6. **Health data** — sleep, vitals, activity, and labs.
7. **Analytics** — deterministic trends and exploratory overlays.
8. **Reports** — physician-report builder and prior snapshots.
9. **Data quality** — drafts, gaps, ambiguities, duplicates, and import health.
10. **Settings & privacy** — integrations, security, retention, export, and deletion.

**Emergency plan** is not buried in this sequence. A text-and-symbol link labeled
“Emergency plan” appears persistently in the application header and on Today. It opens
the server-rendered `/emergency` page directly, not a client-side route. Its access
policy remains the explicit decision tracked by `hc-h0e`; this information architecture
does not resolve that privacy-versus-availability choice implicitly.

### Route map

```text
/
├── /today
├── /timeline
├── /plan
│   ├── /plan/history
│   └── /doses
├── /episodes
│   └── /episodes/:id
├── /symptoms-diary
├── /health-data
│   ├── /sleep-vitals-activity
│   └── /labs
├── /analytics
├── /reports
│   └── /reports/:id
├── /data-quality
├── /settings
└── /emergency              server-rendered outside the SPA
```

On narrow screens, secondary routes appear as tabs or links within their primary page,
not as additional global navigation items. A route change moves focus to the page
heading and announces the new page to assistive technology.

## 2. Global page structure

Each SPA page follows the same reading and keyboard order:

1. Skip link: “Skip to main content.”
2. Application header: product name, emergency link, session menu.
3. Primary navigation.
4. Breadcrumbs only on nested detail pages.
5. One visible level-one page heading.
6. Brief page purpose or current date/range.
7. Status/errors relevant to the whole page.
8. Primary actions.
9. Filters or view controls.
10. Main content.
11. Supporting definitions, provenance, missingness, or help.

Loading, empty, unavailable, error, permission, and filtered-to-zero states are distinct:

- **Loading:** reserves layout and announces “Loading” once; never shows zero values.
- **No records yet:** explains what can be recorded and offers the relevant primary action.
- **No results for filters:** preserves filters and offers “Clear filters.”
- **Provider does not supply metric:** says “Not available from this provider.”
- **Data gap:** names the known time window and does not interpolate it.
- **Service unavailable:** preserves already loaded facts and offers retry/manual workflow.
- **Permission/access state:** explains the required owner action without leaking data.
- **Error:** uses plain language, retains user input when safe, and never includes secrets
  or raw health content in telemetry.

## 3. Category presentation: fact, plan, and AI

Every mixed view exposes the API `category` discriminator programmatically and renders
category with text plus structure. Color may reinforce the treatment but never carries
meaning alone.

| Category | Required visible label | Structural treatment | Example |
|---|---|---|---|
| Recorded fact | **Recorded fact** | Standard record card with source and recorded time | “Dose recorded from Telegram” |
| Physician-approved plan | **Physician-approved plan** | Plan panel with approval/source date and version | “Regimen approved 2026-08-01” |
| AI | **AI-generated observation** or **Unconfirmed AI draft** | Bounded derived-content panel with model/generation metadata | “Draft—review before saving” |

Icons must have text alternatives and are supplementary. AI draft actions are
“Confirm,” “Edit,” and “Cancel”; there is no “Promote to fact/plan” action. Corrections
show “Corrected” and link to revision history. Superseded facts are excluded by default
and visibly marked when history is requested.

## 4. Pages

### 4.1 Today (`/today`)

**Purpose:** answer “What is recorded today, what does the approved plan say, and is
anything awaiting my review?” without implying a medication recommendation.

**Content order:** local date/timezone; current approved-plan summary; actual-versus-plan
dose slots; recorded symptoms/open episode; sleep/activity context; pending drafts;
integration freshness.

**Primary actions:** record dose, record symptom, add note/life event, open or end stress
episode, review pending draft, open emergency plan.

**States:**

- No approved plan: show recorded facts normally and a plan-state message; never infer a
  schedule.
- Approved plan but no doses: say “No doses recorded”; do not render a 0 mg fact.
- No wearable data: show “No data recorded” or provider-unavailable state, not zero.
- Model unavailable: hide no facts; offer deterministic/manual capture and explain that
  free-text extraction is temporarily unavailable.
- Nothing pending: omit the pending-drafts region rather than showing a misleading zero.

### 4.2 Timeline (`/timeline`)

**Purpose:** provide the authoritative chronological view of recorded events and selected
plan changes, with provenance and correction history.

**Primary actions:** filter by date/type/source/category; clear filters; open event detail;
correct an eligible fact; show revision history; add an event; load next page.

**States:** new record with no events; filters returning no matches; an integration gap;
partially failed page load; corrected/superseded history. Stable cursor pagination must
not duplicate items after filter changes.

Each item displays experienced local time and timezone, source, confirmation state, and
category. UTC may appear in detail but is not the primary human-facing time.

### 4.3 Medication plan (`/plan` and `/plan/history`)

**Purpose:** show the current physician-approved regimen, instructions, provenance, and
version history without conflating scheduled slots with actual doses.

**Primary actions:** view current version; compare versions; open source metadata; create
a draft plan through an authorized workflow; retire or approve only through an explicitly
authorized human action. Initial web implementation may remain read-only while approval
stays in the CLI.

**States:** no plan; draft exists but is not approved; current approved plan; retired
plan; future-effective plan; missing provenance. A draft is labeled **Draft plan—not
physician approved** and cannot serve as the adherence baseline.

### 4.4 Doses (`/doses`)

**Purpose:** compare recorded actual doses with the applicable approved-plan version for
a selected day or range.

**Primary actions:** choose range/timezone; record or correct a dose; annotate schedule
relationship; open plan version; inspect calculation definition.

**States:** no approved plan, no recorded doses, plan slot with no matching dose, actual
dose with no scheduled slot, corrected dose, and ambiguous/unconfirmed draft. A missing
slot is derived and never displayed as a recorded zero dose.

### 4.5 Stress episodes (`/episodes`, `/episodes/:id`)

**Purpose:** group stress/up-dose context, symptoms, illness, actual doses, emergency
injections, life events, and recovery without claiming causation or approval.

**Primary actions:** start episode; add/link recorded events; record symptom/dose;
record emergency injection; end episode; add recovery note; compare selected episodes.

**States:** no episodes; one open episode; historical closed episode; incomplete end or
recovery data; linked data unavailable. Up-doses remain recorded facts; physician plan
instructions, when shown, stay in a separately labeled plan region.

### 4.6 Symptoms and diary (`/symptoms-diary`)

**Purpose:** review subjective symptoms, diary notes, and contextual life events with
clear privacy and severity-scale definitions.

**Primary actions:** record symptom; add diary/life event; filter by symptom/tag/severity;
correct a fact; adjust private-entry display; export selected records.

**States:** no entries; filtered-to-zero; an undefined or retired symptom scale; private
text intentionally hidden; unavailable related context. User text is always rendered as
text, never HTML.

### 4.7 Sleep, vitals, and activity (`/health-data/sleep-vitals-activity`)

**Purpose:** show imported sleep start/end and score, heart rate, activity/intensity,
and HRV/stress/body-battery only where the provider supplies them.

**Primary actions:** choose metric/range; switch chart/table; inspect definition,
timezone, source, and last sync; open integration health; request a supported sync.

**States:** integration not connected; provider does not supply metric; never imported;
temporary sync failure; known gap; partial day; value genuinely zero. Gaps remain gaps.
No series is silently interpolated.

### 4.8 Labs (`/health-data/labs`)

**Purpose:** enter, review, confirm, and trend laboratory results while preserving original
value, unit, reference range, specimen time, laboratory, and document/page provenance.

**Primary actions:** enter result; upload/import; review extraction draft; confirm/edit;
choose analyte/range; switch chart/table; open source document where retained.

**States:** no panels; result awaiting confirmation; unreadable PDF/page; OCR unavailable;
missing unit/range; qualitative result; comparable analytes with incompatible units.
Unconfirmed extraction never appears as a recorded lab fact or in analytics.

### 4.9 Analytics (`/analytics`)

**Purpose:** present deterministic metrics and user-selected exploratory overlays with
definitions, timezone, sample size, and missingness.

**Primary actions:** choose metric/range/timezone; add/remove overlay; switch chart/table;
open source records; save a report selection.

**States:** insufficient data; no data; unsupported metric; known gaps; incompatible
units; calculation failure. Every overlay includes the statement that association does
not establish causation. AI summaries, if later added, are optional labeled regions
below deterministic results and cite their input manifest.

### 4.10 Reports (`/reports`, `/reports/:id`)

**Purpose:** build a concise physician-facing report and inspect immutable historical
report snapshots.

**Primary actions:** choose date range/timezone/sections; include patient questions;
explicitly opt in to AI; preview; generate PDF; export CSV/JSON; open prior snapshot.

**States:** no report yet; no approved plan; no records in range; unsupported AI/model
unavailable; generation queued/failed; historical snapshot whose source records were
later corrected. AI is off by default and appears only in a separately labeled section.

### 4.11 Data quality (`/data-quality`)

**Purpose:** make ambiguous drafts, duplicates, missing units/timezones, correction
conflicts, import gaps, and stale integrations actionable without silently changing data.

**Primary actions:** review/edit/confirm/cancel draft; compare possible duplicates;
correct a fact; inspect import batch; retry safe import; open integration settings.

**States:** no findings (“No known data-quality issues”); unresolved finding; service
unavailable; issue intentionally dismissed with audit trail. The page must not imply
that no findings means the record is clinically complete.

### 4.12 Settings and privacy (`/settings`)

**Purpose:** control integrations, authentication, retention, location precision,
exports, deletion, session security, backup status, and audit visibility.

**Primary actions:** connect/disconnect provider; revoke sessions; configure coarse/exact
location consent; request export; configure retention; initiate reviewed deletion;
inspect backup/restore status and security activity.

**States:** provider disconnected/connected/stale/error; token requires reauthorization;
backup status unknown/stale/healthy; deletion unavailable or pending confirmation;
feature not configured. Secrets and raw tokens are never displayed after entry.

### 4.13 Emergency plan (`/emergency`)

**Purpose:** show physician-authored emergency instructions, their author/source and age,
emergency contacts, an emergency-services reminder, and fast confirmed injection logging.

**Runtime:** server-rendered HTML with plain CSS, no SPA bundle, no AI, no integration,
no chart, and no third-party asset or network request. Content remains readable with
JavaScript disabled, Ollama stopped, Redis stopped, jobs stopped, Garmin/weather down,
and the SPA build absent. Per ADR-0011, an anonymous request sees only generic emergency
services/Medical-ID guidance; physician instructions, medications, contacts, and the
injection form require an authenticated owner session.

**Primary actions:** call local emergency services; contact named person; view dated
physician instructions; start injection log; confirm medication/amount/route/time.

**States:** approved current instruction; stale instruction (show age, never hide it);
no approved instruction (show no invented guidance); database unavailable (show a
minimal static emergency-services message if implemented by the later safety design);
injection logging unavailable (instructions remain readable and the failure is explicit).

The page never displays AI summaries or recommendations. Injection logging requires
confirmation and audit but not AI or any external integration.

## 5. Accessibility baseline

The following requirements apply from the first component, not as later remediation:

- WCAG 2.2 AA is the implementation target.
- All functionality is available by keyboard with a logical order matching the visible
  reading order; there are no positive `tabindex` values.
- Focus is visible and is moved deliberately after route changes, modal/dialog opens,
  validation errors, and destructive confirmations. Closing a dialog returns focus to
  its trigger when still present.
- Every page has one programmatically associated `h1`; headings do not skip levels for
  styling.
- Inputs have persistent labels, instructions, units, and errors associated in markup.
  Placeholder text is not a label. Icon-only controls and compact status indicators
  have explicit screen-reader labels that name their action or state.
- Error summaries receive focus, link to invalid fields, and do not discard input.
- Dynamic success/error/loading updates use appropriately restrained live regions.
- Buttons use action names (“Record dose”), not ambiguous labels (“Submit” or icon only).
- Date/time controls expose timezone and do not rely on the browser interpreting a naive
  local timestamp.
- Touch targets are at least 44 by 44 CSS pixels where practical.
- Zoom and text resizing do not hide content or actions.
- Category, severity, confirmation, abnormal flags, and missingness never rely on color
  alone.
- User text, AI output, and imports are rendered as text; `dangerouslySetInnerHTML` is
  prohibited.

## 6. Charts and accessible alternatives

Every chart is one view of the same deterministic dataset as its adjacent alternative.
The chart region includes:

1. A descriptive heading and summary.
2. Metric definition, units, timezone, date range, sample size, and missingness.
3. The chart with a useful accessible name; decorative drawing details stay hidden.
4. A keyboard-operable “View data table” control.
5. A semantic table containing the same displayed values and explicit gap/unavailable
   cells.
6. For multi-series overlays, a written correlation caution and per-series sources.

Keyboard users can change metric/range without traversing every chart point. Tooltips
must also be available by focus or represented in the table. Exported or printed views
retain definitions and timezone.

## 7. Responsive behavior

- Mobile is the capture-first layout: primary action and current status precede charts.
- Desktop may use a persistent sidebar; mobile uses a labeled menu button and focus-
  trapped drawer that returns focus on close.
- Tables may scroll within a labeled region but must not hide row identity or units.
- Dense timeline metadata collapses behind a keyboard-operable details disclosure.
- No critical action is available only through hover, swipe, or a context menu.
- The emergency page uses a single-column, large-type layout at all widths.

## 8. Verification checklist for implementation issues

Each UI issue derived from this map should verify:

- Page purpose and primary actions match this document.
- Loading, empty, filtered-zero, missing, unsupported, and error states relevant to the
  page are covered by tests.
- Fact, plan, and AI treatments have visible text and programmatic category labels.
- Keyboard-only navigation and focus restoration work.
- Automated accessibility checks pass, followed by a short manual keyboard journey.
- Charts have equivalent table/text output and show definition/timezone/missingness.
- No private health data appears in URLs, document titles, logs, analytics, or fixtures.
- The emergency route remains outside and independent of the SPA.

## 9. Open decisions intentionally not resolved here

- Emergency-page access policy (`hc-h0e`).
- Detailed component styling and visual design tokens.
- Exact mobile primary-navigation component.
- Whether source PDFs are retained after confirmed lab extraction.
- Provider- and metric-specific Garmin availability.

These require their existing Beads issues or later ADRs; implementation must not decide
them implicitly.
