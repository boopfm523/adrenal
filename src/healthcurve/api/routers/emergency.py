"""The emergency page.

SAFE-21 requires this to render with Ollama, every integration, and every background
job unavailable. So it is deliberately the least sophisticated thing in the codebase:

* Server-rendered HTML with **no JavaScript**. It works with scripting disabled.
* Reads only ``plan.approved_instruction`` and ``plan.medication``. No AI call, no
  network call, no chart, no queue.
* Instructions are escaped and rendered as text, never as HTML.
* If the database itself is unreachable there is nothing to show, so the page says so
  plainly and still shows the emergency-services advice.

Injection logging posts to a plain HTML form so it works without scripting too.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from healthcurve.api.deps import (
    CurrentOwner,
    CurrentSession,
    DbSession,
    OptionalCurrentOwner,
    OptionalCurrentSession,
)
from healthcurve.episodes.models import EmergencyInjectionEvent
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import from_instant
from healthcurve.identity import service as auth
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseUnit,
    InstructionCategory,
    Medication,
    RegimenStatus,
    RegimenVersion,
)

router = APIRouter(tags=["emergency"])

_EMERGENCY_INJECTION_NAME = "Hydrocortisone Inj Dose"
_EMERGENCY_INJECTION_NORMALIZED_NAME = "hydrocortisone inj dose"
_EMERGENCY_INJECTION_AMOUNT = Decimal("100")

# Inline CSS: an external stylesheet is one more thing that can fail to load.
_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; padding:1rem; font:1rem/1.55 system-ui,-apple-system,Segoe UI,sans-serif;
       background:#fff; color:#111; }
@media (prefers-color-scheme: dark) { body { background:#111; color:#f2f2f2; } }
main { max-width: 44rem; margin: 0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
.urgent { border:3px solid #b30000; background:#fff4f4; color:#111; padding:1rem;
          border-radius:.5rem; margin:0 0 1rem; }
@media (prefers-color-scheme: dark){ .urgent { background:#2a0d0d; color:#fff; } }
.card { border:1px solid #999; border-radius:.5rem; padding:1rem; margin-bottom:1rem; }
.card h2,.none h2 { font-size:1.15rem; margin:0 0 .25rem; }
.urgent p,.none p { margin:.5rem 0 0; }
.meta { font-size:.85rem; opacity:.85; margin:0 0 .5rem; }
.stale { border-left:.5rem solid #a15c00; padding-left:.75rem; }
pre.body { white-space:pre-wrap; font:inherit; margin:0; }
label { display:block; margin:.6rem 0 .2rem; font-weight:600; }
input,select,button { font:inherit; padding:.6rem; width:100%; border-radius:.4rem;
                      border:1px solid #777; }
button { background:#b30000; color:#fff; border:0; font-weight:700; padding:1rem;
         margin-top:1rem; cursor:pointer; }
.none { padding:1rem; border:2px dashed #999; border-radius:.5rem; margin:0 0 1rem; }
a { color:inherit; }
"""


def _page(body: str) -> HTMLResponse:
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Emergency plan — HealthCurve.ai</title>"
        f"<style>{_STYLE}</style></head><body><main>{body}</main></body></html>"
    )
    response = HTMLResponse(html)
    # Never cache emergency content on a shared device (T7).
    response.headers["Cache-Control"] = "no-store"
    return response


_ADVICE = (
    "<div class='urgent'><h1>If this is an emergency, call your local emergency "
    "services now.</h1><p>In the UK dial <strong>999</strong>. In the US dial "
    "<strong>911</strong>. Do not wait for this page, and do not wait for a reply "
    "from anyone. Tell them you have <strong>adrenal insufficiency</strong> and may "
    "be in <strong>adrenal crisis</strong>.</p></div>"
)

_ANONYMOUS_ADVICE = (
    "<div class='urgent'><h1>If this is an emergency, call your local emergency "
    "services now.</h1><p>Do not wait for this page or for a reply from anyone. "
    "Check the person's device Medical ID or physical emergency card and follow "
    "instructions from emergency professionals.</p></div>"
    "<section class='card'><h2>Private emergency details are locked</h2>"
    "<p>Sign in on this device to view physician-authored instructions. HealthCurve "
    "does not reveal a person's medical plan or allow injection logging without an "
    "authenticated owner session.</p></section>"
)


@router.get("/emergency", response_class=HTMLResponse)
def emergency_page(
    session: DbSession,
    owner: OptionalCurrentOwner,
    auth_session: OptionalCurrentSession,
) -> HTMLResponse:
    if owner is None:
        return _page(_ANONYMOUS_ADVICE)

    parts = [_ADVICE]

    version = session.scalar(
        select(RegimenVersion)
        .where(
            RegimenVersion.owner_id == owner.id,
            RegimenVersion.status == RegimenStatus.APPROVED,
        )
        .order_by(RegimenVersion.effective_from.desc())
        .limit(1)
    )

    instructions: list[ApprovedInstruction] = []
    if version is not None:
        instructions = list(
            session.scalars(
                select(ApprovedInstruction)
                .where(
                    ApprovedInstruction.regimen_version_id == version.id,
                    ApprovedInstruction.category.in_(
                        [InstructionCategory.EMERGENCY, InstructionCategory.ILLNESS]
                    ),
                )
                .order_by(ApprovedInstruction.sort_order)
            )
        )

    if instructions:
        parts.append("<h2>Your physician-authored instructions</h2>")
        today = datetime.now(UTC).date()
        for instruction in instructions:
            age_days = (today - instruction.authored_on).days
            # Stale instructions are shown with their age, never hidden (SAFE-22).
            stale = " stale" if age_days > 365 else ""
            age_note = (
                f" &mdash; <strong>written {age_days // 365} year(s) ago; "
                f"check it is still current</strong>"
                if age_days > 365
                else ""
            )
            parts.append(
                f"<section class='card{stale}'>"
                f"<h2>{escape(instruction.title)}</h2>"
                f"<p class='meta'>Written by {escape(instruction.authored_by)} on "
                f"{instruction.authored_on.isoformat()}{age_note}</p>"
                f"<pre class='body'>{escape(instruction.body)}</pre>"
                f"</section>"
            )
    else:
        parts.append(
            "<div class='none'><h2>No physician-authored emergency instructions "
            "recorded</h2><p>HealthCurve will not invent them. Ask your endocrinology "
            "team for written sick-day and emergency injection rules, then add them to "
            "an approved regimen version.</p></div>"
        )

    if auth_session is None:  # pragma: no cover -- owner and session resolve together
        return _page(_ANONYMOUS_ADVICE)
    parts.append(_injection_form(session, owner, csrf_token=auth_session.csrf_token))
    parts.append(
        "<p class='meta'>This page shows recorded facts and physician-authored "
        "instructions only. It contains no generated analysis and works with all "
        "other services offline.</p>"
    )
    return _page("".join(parts))


def _injection_form(session: DbSession, owner: CurrentOwner, *, csrf_token: str) -> str:
    medications = list(
        session.scalars(
            select(Medication).where(
                Medication.owner_id == owner.id,
                Medication.normalized_name == _EMERGENCY_INJECTION_NORMALIZED_NAME,
                Medication.strength == _EMERGENCY_INJECTION_AMOUNT,
                Medication.strength_unit == DoseUnit.MG.value,
            )
        )
    )
    if not medications:
        return (
            "<div class='none'><h2>Log an emergency injection</h2><p>The "
            f"{_EMERGENCY_INJECTION_NAME} 100 mg formulation is not configured. "
            "Add that exact emergency formulation before logging an injection.</p></div>"
        )

    options = "".join(
        f"<option value='{m.id}'>{escape(m.name)} 100 mg</option>" for m in medications
    )
    return (
        "<section class='card'><h2>Log an emergency injection</h2>"
        "<p class='meta'>Recorded as a fact. Log it now; you can add detail later.</p>"
        "<form method='post' action='/emergency/injection'>"
        f"<input type='hidden' name='csrf_token' value='{escape(csrf_token)}'>"
        f"<label for='m'>Medication</label><select id='m' name='medication_id'>{options}</select>"
        "<label for='a'>Amount (mg)</label>"
        "<input id='a' name='amount' type='number' value='100' readonly required>"
        "<label for='s'>Injection site (optional)</label>"
        "<input id='s' name='injection_site' placeholder='outer thigh'>"
        "<label for='b'>Given by (optional)</label>"
        "<input id='b' name='injected_by' placeholder='self'>"
        "<button type='submit'>Log injection now</button>"
        "</form></section>"
    )


@router.post("/emergency/injection")
def log_injection_form(
    session: DbSession,
    owner: CurrentOwner,
    auth_session: CurrentSession,
    csrf_token: str = Form(default=""),
    medication_id: str = Form(),
    amount: str = Form(),
    injection_site: str = Form(default=""),
    injected_by: str = Form(default=""),
) -> RedirectResponse:
    """Form-post injection logging, so it works without JavaScript.

    Uses the moment of submission as the event time -- in an emergency, asking the
    owner to enter a timestamp is the wrong trade. It can be corrected later (SAFE-08).
    """
    if not auth.verify_csrf(auth_session, csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing or invalid CSRF token",
        )

    try:
        parsed_medication_id = uuid.UUID(medication_id)
        parsed_amount = Decimal(amount)
    except (ValueError, InvalidOperation):
        return RedirectResponse("/emergency", status_code=status.HTTP_303_SEE_OTHER)

    medication = session.scalar(
        select(Medication).where(
            Medication.id == parsed_medication_id,
            Medication.owner_id == owner.id,
            Medication.normalized_name == _EMERGENCY_INJECTION_NORMALIZED_NAME,
            Medication.strength == _EMERGENCY_INJECTION_AMOUNT,
            Medication.strength_unit == DoseUnit.MG.value,
        )
    )
    if medication is None or parsed_amount != _EMERGENCY_INJECTION_AMOUNT:
        return RedirectResponse("/emergency", status_code=status.HTTP_303_SEE_OTHER)

    now = datetime.now(UTC)
    events.create_event(
        session,
        EmergencyInjectionEvent,
        owner_id=owner.id,
        event_time=from_instant(now, owner.default_timezone),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=medication.id,
        amount=_EMERGENCY_INJECTION_AMOUNT,
        unit=DoseUnit.MG.value,
        route=medication.default_route.value,
        injection_site=injection_site or None,
        injected_by=injected_by or None,
    )
    return RedirectResponse("/emergency?logged=1", status_code=status.HTTP_303_SEE_OTHER)
