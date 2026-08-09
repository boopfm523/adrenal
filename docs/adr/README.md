# Architecture Decision Records

One file per decision, numbered and immutable once `Accepted`. To change a decision,
write a new ADR that supersedes the old one and edit the old one's status line to
`Superseded by ADR-nnnn`. Never rewrite an accepted decision in place.

Format: Status, Context, Decision, Consequences, Alternatives considered.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-postgresql-datastore.md) | PostgreSQL as the datastore | Accepted |
| [0002](0002-modular-monolith-topology.md) | Modular monolith on Docker Compose behind Caddy | Accepted |
| [0003](0003-private-ollama-connectivity.md) | Private Ollama connectivity | Accepted |
| [0004](0004-job-queue-behind-interface.md) | Database-backed job queue behind an interface | Accepted |
| [0005](0005-react-spa-frontend.md) | React + TypeScript + Vite frontend | Accepted |
| [0006](0006-python-runtime-version.md) | Python 3.13 as the pinned runtime | Accepted |
| [0008](0008-telegram-long-polling.md) | Telegram long polling as the default transport | Accepted |

Per `docs/safety-spec.md`, changing or removing a `SAFE-nn` rule also requires an ADR
here.
