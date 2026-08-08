# ADR-0006: Python 3.13 as the pinned runtime

**Status:** Accepted — 2026-08-08

## Context

The plan states: "Python 3.12/3.13 … Python 3.14 may be used only after dependency
compatibility is demonstrated" (§5), and "Pin dependencies and verify runtime versions
in CI".

The development machine's system Python is **3.14.4** — the only interpreter present
before this build began. Using it would violate the plan's constraint without the
demonstration the plan requires. Meanwhile the dependency set (SQLAlchemy 2, Pydantic,
psycopg, Playwright) has to work identically in dev, CI, and production containers, and
a mismatch between the developer's interpreter and the deployed one is exactly the kind
of difference that produces a bug nobody can reproduce.

## Decision

**Python 3.13 is the pinned runtime for the application in every environment**, managed
by `uv`.

1. `.python-version` pins `3.13`; `pyproject.toml` declares `requires-python = ">=3.13,<3.14"`.
2. `uv` provisions the interpreter — the developer does **not** use the system 3.14.
   `uv python install 3.13` makes dev match CI and production without touching the
   system Python.
3. The production container image is pinned by digest to a 3.13 base.
4. **CI asserts the running interpreter version**, failing the build if it is not 3.13.
   This is the plan's "verify runtime versions in CI" requirement made concrete, and it
   is what stops a future base-image bump from silently changing the runtime.
5. Dependencies are pinned in `uv.lock` with hashes, committed to the repository.
6. **Moving to 3.14 requires a superseding ADR** carrying the demonstration the plan
   asks for: the full test suite green on 3.14 against real PostgreSQL, every pinned
   dependency resolving and importing, and Playwright PDF rendering verified. Until
   that evidence exists, 3.14 is not used — including on the developer's machine.

Rationale for 3.13 over 3.12: both are permitted, 3.13 has the longer remaining support
window, and it narrows the gap to the eventual 3.14 migration.

## Consequences

Positive:

- Dev, CI, and production run the same interpreter, so "works on my machine" is not a
  category of bug this project has.
- The version constraint is enforced by CI rather than by everyone remembering it.
- The 3.14 question becomes a tracked, evidence-bearing decision instead of an
  accident of whatever the host had installed.

Negative / costs:

- A second Python on the development machine, and `uv` as a required tool. Small, and
  `uv` is already needed for locking.
- The project lags the newest interpreter, forgoing 3.14's improvements until the
  compatibility demonstration is done. Deliberate: the plan ranks dependency
  reliability above interpreter novelty, and so does a health record.
- The pin needs periodic revisiting so it does not silently become an unsupported
  version. The 3.14 evaluation is tracked as its own issue rather than left implicit.

## Alternatives considered

**Use the system 3.14.4.** Zero setup, newest runtime. Rejected: it inverts the plan's
explicit ordering — compatibility must be demonstrated *before* adoption, and no such
demonstration exists yet. Adopting it by default would also make the developer's
environment differ from any pinned container base.

**Python 3.12.** Equally permitted and marginally more mature in the ecosystem.
Rejected only on support window; 3.13 is the better place to sit for a project with a
multi-year horizon.

**Do not pin; accept any 3.12–3.14.** Rejected. A range means CI and production can
diverge, and a decimal-arithmetic or datetime behavior difference between interpreters
is precisely the kind of thing that must not vary underneath a medication record.

**pyenv or conda instead of uv.** Workable. `uv` chosen because it already handles
locking with hashes and resolves an order of magnitude faster, so one tool covers both
interpreter and dependency pinning.
