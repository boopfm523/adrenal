# ADR-0011: Tiered emergency-page access without public medical details

**Status:** Accepted — 2026-08-09

## Context

The emergency page originally required an authenticated owner session. That protected
physician-authored instructions and medication details, but returned an unhelpful 401
when a family member or responder opened the page without the owner's session. Removing
authentication entirely would disclose a private medical plan to anyone with the URL
and would expose a high-impact injection write action.

HealthCurve is Tailscale-only under ADR-0007, but tailnet reachability is not medical-
data authorization. ADR-0007 also establishes that emergency instructions cannot rely
on HealthCurve availability. Platform Medical ID, a physical emergency card, and local
emergency services remain the responder-facing sources that work with a locked device,
an unavailable host, or no tailnet connection.

## Decision

`GET /emergency` has two deterministic server-rendered views:

1. Without a valid owner session, return HTTP 200 with only generic advice to contact
   local emergency services and check the person's device Medical ID or physical card.
   Do not reveal identity, diagnosis, medications, physician instructions, contacts,
   or recorded events. Do not render an injection form.
2. With a valid owner session, render the existing dated physician-authored emergency
   instructions and owner medications. This remains a convenience view, not the sole
   emergency record.

All responses remain `no-store`, contain no JavaScript or third-party request, and make
no AI/integration/job call. `POST /emergency/injection` remains authenticated; an
anonymous visitor cannot create a fact. A signed long-lived emergency link is not
introduced because it would be a bearer credential that is easy to copy, difficult to
revoke during a crisis, and still unusable when the host or tailnet is unavailable.

## Consequences

- An unauthenticated person receives useful next-step guidance instead of a 401.
- Private plan and medication data remain owner-authenticated.
- The application clearly directs responders to Medical ID/physical material that can
  work when HealthCurve cannot.
- A signed-in owner can still hand an unlocked device to a responder to show the full
  plan.
- Users must maintain an external emergency record; HealthCurve cannot solve locked-
  device or service-outage access.

## Alternatives considered

**Make the full page unauthenticated.** Rejected because URL knowledge would disclose
high-sensitivity medical data and expose a write form.

**Use a signed long-lived link.** Rejected for now because a bearer URL is copyable,
can leak through browser history, and does not address host, network, or locked-device
availability.

**Keep returning 401.** Rejected because it provides no safe instruction at the exact
moment the route is intended to help.
