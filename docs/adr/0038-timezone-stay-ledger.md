# ADR-0038: The owner's timezone is a ledger over time, not a field

**Status:** Accepted — 2026-09-23.

## Context

`owner.default_timezone` was the only zone the application had. It was set once at
owner creation and editable nowhere. Every capture and display path resolved against
it, so an entry made while travelling was recorded and labelled in the home zone.

That is not a cosmetic error on a cortisol curve. A stated wall time — "took it at
8am", the most natural way to record a dose — was converted using the *home* offset,
placing the instant hours from when the dose was actually taken. The modelled peak
moves with it, and the owner has no way to tell from the record that it did. The same
day's doses recorded from Telegram abroad and from the browser at home would disagree
with each other while both looked correct.

Making the field editable would have been the small change. It is also the wrong one:
changing it rewrites the meaning of every entry already recorded against it. A
person's zone is not a setting, it is a history.

## Decision

### 1. A stay ledger, not a mutable current zone

`identity.timezone_stay` holds one row per stay: an owner, an IANA zone, the instant
the stay began, how it was recorded (`telegram`, `web`, `cli`), and an optional label.

A stay has no end. It ends when the next one begins. This makes the zone a step
function over time, and makes a gap or an overlap *unrepresentable* rather than merely
prevented — there is no end column to disagree with the next row's start. A unique
constraint on `(owner_id, started_at)` forbids two stays at one instant.

A mutable `current_timezone` field with an audit trail alongside would have stored the
same information in two places, and the one consulted at read time would have been the
one that could not answer "which zone was in force last Tuesday".

### 2. No backfill

An instant *before* the first recorded stay resolves to `owner.default_timezone`. That
is exactly the behaviour that existed before the ledger, so introducing it reinterprets
no existing event. Nothing already recorded moves.

The alternative — seeding a first stay at owner creation — would have been tidier and
strictly worse: it invites a later migration to "correct" historical zones from
evidence nobody has.

### 3. `default_timezone` keeps its meaning and becomes *home*

It is where the owner lives, not where they are. Travel does not change it. Both values
are reported side by side everywhere they appear, because conflating them is the
original bug.

### 4. Resolution has three distinct questions

- `zone_at(instant)` — the zone in force at an instant. Used for *display*: a dose
  taken in Chicago keeps reading back as the Chicago wall time after the owner flies
  home, which is what makes "I took it at 8" still true.
- `current_zone()` — the zone right now. Used for capture defaults, local date
  windows, analytics and report ranges, and the Garmin scheduler's local sync day.
- `zone_for_local_time(local)` — the zone a *stated* wall time belongs to. Resolved by
  converging: convert under the current zone, look up the zone in force at the
  resulting instant, convert again under that. Two passes settle every case the ledger
  can produce, because the first pass can only be wrong if the instant crossed a stay
  boundary and the second converts inside the zone that boundary leads to.

The third exists because backfilling is genuinely ambiguous: choosing the zone is what
fixes the instant, and the instant is what selects the zone. It returns only *which
zone to ask*; the authoritative `EventTime` is still resolved by the caller, which
surfaces DST ambiguity rather than guessing.

### 5. Places resolve offline, and never silently

A zone is set by naming a place ("Chicago", "just landed in Denver") or an IANA zone.
Resolution uses tz-database city segments plus a hand-written alias table, with no
geocoding call — asking a third party to convert a place name would tell that third
party where the owner is, which is what the privacy model exists to prevent.

A name matching nothing resolves to nothing and the caller says so; a name matching
zones that genuinely differ raises with the candidates rather than picking one. In
Telegram an unrecognised phrase falls through to extraction untouched, which is what
keeps "I'm in pain" from being read as a journey.

### 6. Recording is always asked for

The web UI knows the device zone from `Intl.DateTimeFormat().resolvedOptions()`, and
offers to switch when it differs from the recorded one. It never switches on its own.
A device clock set wrong would otherwise reinterpret every entry made afterwards
without anyone being asked, and the resulting record would look entirely ordinary.

Restating the zone already in force records no row. Saying "I'm in Chicago" twice is a
restatement, not travel, and a ledger accumulating a row per restatement makes its own
history unreadable.

## Consequences

Analytics grouped by local day across a trip group by *the day the owner was living
in*. A westward flight can therefore produce a longer local day and an eastward one a
shorter one, and two doses can share a local hour across a boundary. This is correct
and is the point: the alternative is a uniform day that matches no lived day at either
end.

Zones are only as accurate as what was recorded. A trip never mentioned to HealthCurve
is recorded in the previous zone, exactly as before the ledger existed. The failure
direction is deliberate — under-recording leaves data as it was, while inference would
silently rewrite it.

`identity.saved_coarse_location.timezone` stores the zone in force when the place was
saved, so the coordinates and the zone describe the same place.

## Alternatives considered

**Editable `owner.default_timezone`.** Rejected: changing it retroactively changes what
every stored event means, and there is no record of what it used to be.

**Store a UTC offset per event and drop zones.** Rejected: an offset cannot answer what
the wall time will be after a DST transition, and the tz database is the thing that
knows.

**Infer the zone from the device, or from a shared phone location.** Rejected: an
inferred zone is indistinguishable from a stated one once stored, and both signals are
wrong often enough to corrupt a curve. The device zone is an *offer* (§6); a shared
coarse location is context, not a journey.

**Close each stay with an explicit `ended_at`.** Rejected: two columns can disagree.
Open-ended stays make the invariant structural.
