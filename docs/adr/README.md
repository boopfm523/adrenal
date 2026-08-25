# Architecture Decision Records

One file per decision, numbered and immutable once `Accepted`. To change a decision,
write a new ADR that supersedes the old one and edit the old one's status line to
`Superseded by ADR-nnnn`. Never rewrite an accepted decision in place.

Format: Status, Context, Decision, Consequences, Alternatives considered.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-postgresql-datastore.md) | PostgreSQL as the datastore | Accepted |
| [0002](0002-modular-monolith-topology.md) | Modular monolith on Docker Compose behind Caddy | Accepted; edge portions superseded by 0007 |
| [0003](0003-private-ollama-connectivity.md) | Private Ollama connectivity | Superseded by 0017 |
| [0004](0004-job-queue-behind-interface.md) | Database-backed job queue behind an interface | Accepted |
| [0005](0005-react-spa-frontend.md) | React + TypeScript + Vite frontend | Accepted |
| [0006](0006-python-runtime-version.md) | Python 3.13 as the pinned runtime | Accepted |
| [0007](0007-tailscale-only-hosting.md) | Tailscale-only hosting with no public edge | Accepted |
| [0008](0008-telegram-long-polling.md) | Telegram long polling as the default transport | Accepted |
| [0009](0009-two-database-roles-per-operation.md) | Two database roles, chosen per operation | Accepted |
| [0010](0010-local-document-ingestion-and-models.md) | Deterministic-first local document ingestion and task-specific models | Accepted |
| [0011](0011-tiered-emergency-page-access.md) | Tiered emergency-page access without public medical details | Accepted |
| [0012](0012-unofficial-garmin-connect-read-only.md) | Isolated read-only use of the unofficial Garmin Connect client | Superseded by 0014 |
| [0013](0013-theoretical-steroid-exposure-model.md) | Versioned theoretical steroid-exposure model from recorded doses | Accepted |
| [0014](0014-garmin-intraday-read-contract.md) | Read-only Garmin intraday metric contract | Accepted |
| [0015](0015-recorded-context-not-cortisol-demand.md) | Recorded context overlays without inferred cortisol demand | Accepted |
| [0016](0016-garmin-sleep-interval-contract.md) | Explicit Garmin sleep intervals on the selected-day HealthCurve | Accepted |
| [0017](0017-host-native-ollama-default.md) | Host-native Ollama is the owner runtime default | Accepted |
| [0018](0018-population-cortisol-reference-band.md) | Do not compare relative hydrocortisone exposure with population cortisol reference bands | Accepted |
| [0019](0019-regimen-effective-time-provenance.md) | Canonical regimen instants with preserved local-time provenance | Accepted |
| [0020](0020-on-demand-day-analysis-projection.md) | On-demand daily AI analysis uses a fingerprinted projection | Accepted |
| [0021](0021-versioned-wearable-daily-summaries.md) | Versioned wearable daily summaries for bounded longitudinal reads | Accepted |
| [0022](0022-durable-streamed-private-exports.md) | Durable streamed private exports | Accepted |
| [0023](0023-indefinite-hot-wearable-retention.md) | Indefinite exact wearable retention | Accepted |
| [0024](0024-selectable-physiological-cortisol-scenario-model.md) | Selectable physiological cortisol scenario model without dosing-adequacy claims | Accepted; default-selection portion superseded by 0028 |
| [0025](0025-private-health-data-chatbot.md) | Private health-data chatbot uses bounded read-only domain tools | Accepted |
| [0026](0026-wake-anchored-free-cortisol-reference-and-meals.md) | Wake-anchored free-cortisol reference and observed meal context | Accepted |
| [0027](0027-evidence-versioned-50mg-iv-push-hydrocortisone-model.md) | Evidence-versioned 50 mg and 100 mg IV-push hydrocortisone model | Accepted |
| [0028](0028-full-cortisol-model-default.md) | Full cortisol model v4 as the Daily Review default | Accepted |
| [0029](0029-public-static-healthcurve.md) | One-way public static HealthCurve mirror | Accepted |

Per `docs/safety-spec.md`, changing or removing a `SAFE-nn` rule also requires an ADR
here.
