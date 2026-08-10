# HealthCurve

A private personal health record and analysis application, focused on living without
adrenal glands and managing adrenal insufficiency.

HealthCurve is **not** a diagnostic product, an emergency service, or an autonomous
medication adviser. Its first obligation is a trustworthy longitudinal record.
Analytics and AI are derived views, never substitutes for medical facts or a
physician-approved plan.

## The three categories

Everything stored belongs to exactly one of three categories, kept separate in
storage, API, UI, exports, and reports:

| | Authority | Can AI create it? |
|---|---|---|
| **Recorded fact** | The user and their devices | No |
| **Physician-approved plan** | A clinician, with provenance | No |
| **AI analysis** | Derived only, always labeled and cited | Yes |

These boundaries are specified in [docs/safety-spec.md](docs/safety-spec.md) as 29
numbered rules (`SAFE-01`…`SAFE-29`) and enforced by tests that name their rule ID.
CI fails if a rule marked `enforced` loses its coverage.

## Documentation

| Document | What it is |
|---|---|
| [docs/HealthCurve_Project_Plan.md](docs/HealthCurve_Project_Plan.md) | Product intent and architecture (the source document) |
| [docs/safety-spec.md](docs/safety-spec.md) | Normative safety rules `SAFE-01`…`SAFE-29` |
| [docs/safety-rules.yaml](docs/safety-rules.yaml) | Machine-readable rule index used by the CI gate |
| [docs/threat-model.md](docs/threat-model.md) | Threats `T1`…`T7` and data classification `C0`…`C13` |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/roadmap.md](docs/roadmap.md) | What is left to build, and why it is ordered that way |
| [docs/beads-workflow.md](docs/beads-workflow.md) | Verified Beads commands and the working loop |
| [docs/telegram-setup.md](docs/telegram-setup.md) | Step-by-step guide to connecting the Telegram bot |
| [docs/credential-encryption.md](docs/credential-encryption.md) | Store, rotate, and recover integration encryption keys |
| [docs/garmin-import.md](docs/garmin-import.md) | Review and import owner-exported Garmin FIT/CSV data |
| [docs/backup-runbook.md](docs/backup-runbook.md) | Encrypted backup setup, monitoring, retention, and recovery operations |

Build status lives in Beads, not in this file. Run `bd ready` to see claimable work, and
read [docs/roadmap.md](docs/roadmap.md) for what remains and in what order.

## Requirements

- [uv](https://docs.astral.sh/uv/) — provisions Python 3.13 (ADR-0006) and locks deps
- Docker with Compose — PostgreSQL, Redis, Ollama, Caddy
- Node.js 24 or newer — builds and tests the locked React client
- [Beads](https://github.com/gastownhall/beads) (`bd`) — issue tracking, mandatory

The host's system Python is not used. `uv` installs and pins 3.13.

## Getting started

```bash
make setup                 # pinned Python and frontend dependencies
cp .env.example .env       # then set POSTGRES_PASSWORD and POSTGRES_AI_PASSWORD

make check                 # lint, types, module boundaries, tests
make up                    # start the local stack
make migrate               # apply migrations (never automatic -- ADR-0002)
```

`make help` lists every target.

The web client is a React/TypeScript SPA in `frontend/`; the emergency page remains
server-rendered by FastAPI. `make frontend-generate` refreshes the committed OpenAPI
contract and generated types, and `make frontend-check` runs its contract, lint, test,
and production-build gates. See [docs/web-frontend-guide.md](docs/web-frontend-guide.md).

### Putting your own data in

Everything below runs inside the `api` container, which is on the private network.

```bash
# 1. Your account. HealthCurve is single-owner; this can only be run once.
docker compose run --rm api python -m healthcurve.cli create-owner \
    --email you@example.com --timezone Europe/London

# 2. A template for your medications and schedule.
docker compose run --rm -v "$PWD:/out" api \
    python -m healthcurve.cli init-medications-file /out/medications.yaml

# 3. Fill medications.yaml in from your prescription and your physician's written
#    instructions, then load it. This creates a DRAFT regimen.
docker compose run --rm -v "$PWD/medications.yaml:/tmp/m.yaml:ro" api \
    python -m healthcurve.cli load-medications /tmp/m.yaml

# 4. Approve it. A draft is never treated as your plan, and approval requires
#    naming who approved it and where it came from (SAFE-16).
docker compose run --rm api python -m healthcurve.cli approve-regimen <id> \
    --by "Dr Name, Endocrinology" --source "clinic letter 2026-01-01"
```

Then connect Telegram by following [docs/telegram-setup.md](docs/telegram-setup.md).

If an automated development bootstrap created the owner but its credentials were not
handed off, use the reviewed local recovery command below. It updates the same owner
row and revokes all sessions; it does not delete health data. The command refuses to
run outside development or after MFA has been enrolled, and the password is accepted
only at a hidden prompt.

```bash
docker compose run --rm api python -m healthcurve.cli recover-owner-access
```

Normal operation uses the login page's password-change and MFA recovery flows; this
command is only for correcting an inaccessible development bootstrap account.

## Layout

```text
src/healthcurve/
  app.py         FastAPI application factory (entry point)
  cli.py         operator commands: owner, medications, credentials, Telegram
  api/           routers, request/response schemas, shared dependencies
  identity/      owner account, sessions, authorization
  events/        canonical timeline, corrections, provenance
  medications/   medications, plan versions, dose slots, instructions
  episodes/      stress/up-dose and emergency injection workflows
  integrations/  Telegram, Garmin, location/timezone, weather
  labs/          panels, analytes, ranges
  ai/            extraction drafts, model registry, analysis, safety gate
  analytics/     deterministic metrics and aggregations
  reports/       snapshots, rendering, exports
  operations/    jobs, import batches, audit, backup status
```

Module boundaries follow ADR-0002 and are enforced in CI by an import-linter
contract — a cross-module import that bypasses a module's public interface fails
the build.

## Safety notes for contributors

- Never commit secrets or real health data. Fixtures are synthetic only (`SAFE-29`).
- A correction supersedes; it never overwrites (`SAFE-08`).
- Missing data is never stored as zero (`SAFE-10`).
- AI code paths cannot write facts or plans — this is a database privilege, not a
  convention (`SAFE-15`, `SAFE-16`).
- The emergency page must render with AI, integrations, and jobs all down
  (`SAFE-21`).
- No untracked implementation work. Pull the next task with `bd ready`.
