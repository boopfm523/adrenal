# Beads Workflow for HealthCurve

This document records the **verified** Beads commands for this repository, as required by
`docs/HealthCurve_Project_Plan.md` §16. The plan's own examples were explicitly marked
illustrative; everything below was run against the installed binary.

## Installed version

| Item | Value |
|---|---|
| Binary | `bd` (`/opt/homebrew/bin/bd`) |
| Version | `bd version 1.1.2 (Homebrew)` |
| Install method | `brew install beads` (formula `beads`, homepage `github.com/gastownhall/beads`) |
| Backend | Dolt, embedded mode (SQLite backend removed upstream) |
| Database / prefix | `hc` — issues are named `hc-<hash>` |
| Repo root | `$HOME/Documents/adrenal` |

Verify with:

```bash
bd --version
bd context      # effective backend identity and repository context
bd info         # database information
```

## Initialization (already done)

```bash
BD_NON_INTERACTIVE=1 bd init --prefix hc --non-interactive
```

This created `.beads/`, the embedded Dolt database, `AGENTS.md`, and a Claude Code
skill under `.agents/skills/beads/`. It also made an initial git commit of the
beads files.

> **Note:** there is no Dolt remote configured. `.beads/issues.jsonl` is an *export*,
> not the source of truth and not cross-machine sync. If this repo ever gets an
> `origin`, run `bd dolt push` for durable sync. Until then the Dolt database in
> `.beads/` is the source of truth and must be backed up with the repo.

## Vocabulary supported by 1.1.2

### Statuses (`bd statuses`)

| Beads status | Category | Plan concept (§16) |
|---|---|---|
| `open` | active | `backlog` and `ready` — a `open` issue with no active blockers *is* ready |
| `in_progress` | wip | `in_progress` |
| `blocked` | wip | `blocked` |
| `deferred` | frozen | intentionally parked |
| `closed` | done | `done` |
| `pinned` | frozen | persistent reference issues |
| `hooked` | wip | attached to an agent hook |

The plan's `ready` and `review` concepts have **no dedicated status** in 1.1.2:

- **ready** is *derived*, not stored. `bd ready` returns open issues with no active
  blockers. Never hand-set a "ready" status.
- **review** is represented with the label `state:review` on an `in_progress` issue
  (see Labels below). Do not close an issue to mean "in review".

### Types (`bd types`)

`task` (default), `bug`, `feature`, `chore`, `epic`, `decision`, `spike`, `story`,
`milestone`. The plan's `type:spike` / `type:chore` / `type:bug` labels are therefore
**native types** here — use `--type`, not a label.

### Dependency relationships

```bash
bd dep add <blocked-id> <blocker-id>        # blocked-id depends on blocker-id
bd dep add <blocked-id> --blocked-by <id>   # same thing, explicit
bd dep <blocker-id> --blocks <blocked-id>   # inverse phrasing, same edge
bd dep list <id>                            # dependencies / dependents
bd dep tree <id>                            # dependency tree
bd dep cycles                               # detect cycles
bd dep relate <a> <b>                       # informational, non-blocking link
bd dep unrelate <a> <b>
```

Per the plan's distinction: `dep add` / `--blocks` are the **required** blocking
relationships; `dep relate` is the **informational** link that must never gate work.

Hierarchy is separate from dependency: `--parent <epic-id>` on `bd create` makes a
child bead. `bd children <epic-id>` lists them; `bd epic` has epic-level commands.
Child IDs are hierarchical (`hc-34v.2` is the second child of epic `hc-34v`).

### Constraint: epics can only be blocked by epics

Verified in 1.1.2 — `bd dep add <epic> <task>` fails with:

```
Error: epics can only block other epics, not tasks
```

This changes how the plan's §16 phase gates are expressed. "Phase 1 must depend on
the safety specification and canonical schema" **cannot** be an edge from a task to
the `HC-P1` epic. Instead:

- **Epic → epic** edges express phase ordering: `HC-P2` and `HC-P3` depend on `HC-P1`;
  `HC-P6` depends on `HC-P5`.
- **Task-level gates** are wired onto the *child tasks* of the gated epic. Every
  `HC-P1` child that touches schema or clinical data is `--blocked-by` the safety
  spec and the canonical-event spike. Every `HC-P4` child is `--blocked-by` the
  Garmin feasibility spike. The `HC-P5` release task is `--blocked-by` the security
  review and the restore drill.

Consequence: **when creating a child of a gated epic, wire its gates at creation
time.** The epic itself carries no protection.

## Labels used in this repository

Native types cover spike/bug/chore, so labels carry the remaining axes from §16:

- Phase: `phase:P0` … `phase:P6`
- Area: `area:meds`, `area:events`, `area:web`, `area:telegram`, `area:garmin`,
  `area:ai`, `area:reports`, `area:ops`, `area:labs`, `area:auth`
- Risk: `risk:clinical`, `risk:privacy`, `risk:security`
- Workflow state not covered by status: `state:review`

Children inherit labels from `--parent` unless `--no-inherit-labels` is passed.

```bash
bd label --help      # manage labels
bd tag <id> <label>  # add a label
```

## The required working loop (§16), as concrete commands

```bash
# 1. Query ready/unblocked work
bd ready
bd ready --json
bd list --status open --label phase:P1

# 2/3. Inspect before claiming
bd show <id>
bd dep tree <id>

# 4. Claim it and record the intended approach
bd update <id> --status in_progress
bd note <id> "Approach: ..."

# 6. Record discoveries, decisions, blockers as they happen
bd comment <id> "Discovery: ..."
bd update <id> --status blocked        # when genuinely blocked
bd dep add <id> <new-blocker-id>

# 7. New work discovered mid-task -> tracked, not invisible
bd create "..." --type task --labels phase:P1,area:meds \
  --deps discovered-from:<id>

# 8/9. Evidence, review, close
bd comment <id> "Evidence: pytest 42 passed; alembic upgrade head clean"
bd tag <id> state:review
bd close <id>

# 10. Next
bd ready
```

Useful extras verified present: `bd status` (database overview), `bd graph`,
`bd search <text>`, `bd query`, `bd lint` (missing template sections),
`bd stale`, `bd history <id>`, `bd export`, `bd import`, `bd backup`.

## Creating issues

```bash
bd create "Title" \
  --type task \
  --priority 1 \
  --parent hc-<epic> \
  --labels phase:P1,area:meds,risk:clinical \
  --description "Outcome ..." \
  --acceptance "Observable criteria ..." \
  --design "Scope and explicit exclusions ..." \
  --notes "Test/verification requirements ..."
```

Priority is `0`–`4` (`0` = highest), default `2`.

Long fields come from files or stdin when needed: `--body-file`, `--design-file`,
`--stdin`. Batch creation: `bd create --file <markdown>` or `--graph <json>`.
`bd create --dry-run` previews without writing.

## What gets committed to git

`bd init` already committed the beads files it wanted tracked. Follow that: commit
`.beads/` contents that beads itself writes and stages, plus `AGENTS.md`. Never
commit secrets, caches, machine-specific paths, or real health data — see
`docs/safety-spec.md` and `.gitignore`.

Auto-export of `.beads/issues.jsonl` is **off** by default in 1.1.2. Enable with
`bd config set export.auto true` if a plain-text view of issues in git is wanted.

## Rules for this build

1. No untracked implementation work. Every change traces to an issue.
2. Pull work with `bd ready` only — never invent the next task.
3. `in_progress` before editing code; evidence comment before `bd close`.
4. Discovered work becomes a linked issue (`discovered-from:`), never a silent fix.
5. An epic closes only when its children are closed.
