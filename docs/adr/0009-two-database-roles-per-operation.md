# ADR-0009: Two database roles, chosen per operation

Status: Accepted

## Context

SAFE-15 and SAFE-16 say the AI must never write a fact or a plan, and ADR-0001 made
that a database privilege rather than a code convention: the `healthcurve_ai` role
holds no INSERT, UPDATE or DELETE on the `fact` and `plan` schemas.

The first implementation chose the role **per process**. The API container connected
as the privileged role; the worker container, which runs AI jobs, connected as the
restricted one (`docker-compose.yml`). That looked like a clean separation.

It was the wrong boundary, and the running system proved it. Every inbound Telegram
message failed with `permission denied for schema identity`: the worker must read
`identity.owner` to handle any message at all, and the restricted role has no access
to that schema — correctly, since it holds the owner's email and password hash.

The deeper problem only showed up on inspection. The worker is also the process that
handles **confirmation**. Pressing *Confirm* on a draft is the owner's action, and it
writes a fact. Under the restricted role it could never have succeeded. The rule meant
to stop the AI from writing facts was instead stopping the human, while the model kept
writing drafts through that same connection regardless.

A process is not a principal. The worker performs both AI work and privileged work, so
no single role fits it.

## Decision

Bind the role to the **operation**, not the process.

- Every process connects as the privileged role by default (`HC_DATABASE_URL`).
- A second engine, `HC_AI_DATABASE_URL`, is bound to the restricted role and used at
  exactly one place: the write that turns model output into a draft
  (`_handle_free_text` in the Telegram handlers). `db.get_ai_session_factory()`
  provides it.
- The deterministic command paths (`/dose`, `/symptom`, `/injection`) keep the
  caller's session. They are not model output, and routing them through the AI role
  would falsely imply the model produced them.
- In production, `HC_AI_DATABASE_URL` is mandatory, and pointing it at the same URL as
  `HC_DATABASE_URL` is refused. In development an unset value falls back to the
  privileged connection and logs `reason_code=ai_role_not_separated`, so the downgrade
  is visible rather than silent.

## Consequences

- The guarantee is now narrower but true: the connection carrying model output is
  denied writes to `fact` and `plan`, verified live (`ProgrammingError` on an attempted
  insert) and in `tests/integration/test_schema_privileges.py`.
- The owner can confirm drafts, which the previous design made impossible.
- A draft is written in its own transaction on a separate connection, so it commits
  independently of the message-handling transaction. Acceptable: a draft is not a fact,
  and a duplicate or orphaned draft is recoverable — `_store_draft` already supersedes
  any earlier pending draft.
- Two connection pools instead of one. Negligible for a single-owner deployment.
- The correctness of the boundary now depends on call sites choosing the right session.
  `tests/unit/test_ai_session_separation.py` pins both directions — the free-text path
  must use the AI session, the command paths must not — and the import-linter contract
  `ai-cannot-reach-write-paths` remains as defence in depth.
- `identity` stays unreadable to the AI role. That denial is a feature, not the bug it
  first appeared to be.

## Alternatives considered

**Grant the AI role access to `identity` and `fact`.** The one-line fix for the visible
symptom. Rejected: it would have given the model's connection read access to a password
hash and write access to the record, deleting the strongest safety property in the
design to fix an error message.

**Keep the worker restricted and move confirmation to the API.** Confirmation would
become a cross-process call for a button press that is already in the worker's hands.
It also splits one user-visible action across two services for no safety gain — the
model still would not be the thing writing the fact either way.

**Drop the AI role entirely and rely on the import-linter contract.** Rejected: that
converts an enforced privilege into a convention. The plan's position (§5) is that
safety rules must be structural, and a linter does not constrain a running process.
