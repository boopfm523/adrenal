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
| [docs/beads-workflow.md](docs/beads-workflow.md) | Verified Beads commands and the working loop |

Build status lives in Beads, not in this file. Run `bd ready` to see claimable work.

## Requirements

- [uv](https://docs.astral.sh/uv/) — provisions Python 3.13 (ADR-0006) and locks deps
- Docker with Compose — PostgreSQL, Redis, Ollama, Caddy
- [Beads](https://github.com/gastownhall/beads) (`bd`) — issue tracking, mandatory

The host's system Python is not used. `uv` installs and pins 3.13.

## Getting started

```bash
uv python install 3.13     # pinned runtime (ADR-0006)
uv sync --dev              # install locked dependencies
cp .env.example .env       # then edit; .env is git-ignored

make check                 # lint, types, module boundaries, tests
make up                    # start the local stack
```

`make help` lists every target.

## Layout

```text
src/healthcurve/
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
