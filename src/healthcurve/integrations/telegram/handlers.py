"""Telegram message handling: commands, extraction drafts, confirmation.

The flow for free text is: receive -> extract -> validate -> show draft -> confirm,
edit, or cancel -> store -> reply with what was recorded. Nothing becomes a fact
before the confirm step (SAFE-11, SAFE-12).

Deterministic slash commands exist so the bot stays useful when the model is down
(ADR-0003). They are the fallback the plan requires, and they never touch the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.ai.extraction import (
    BLOCKING_FLAGS,
    CandidateType,
    FlagCode,
    ValidatedCandidate,
    extract,
)
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.ai.ollama import ModelOutcome, OllamaClient
from healthcurve.db import get_ai_session_factory
from healthcurve.episodes.models import EmergencyInjectionEvent, EpisodeStatus, StressEpisode
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import DiaryEvent, SymptomEvent
from healthcurve.events.timekeeping import (
    AmbiguousLocalTimeError,
    NonExistentLocalTimeError,
    from_instant,
    resolve_event_time,
)
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import location
from healthcurve.medications import service as meds
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Medication, Route
from healthcurve.operations.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitUnavailable,
)

#: A draft the owner never answers is purged rather than left to be confirmed days
#: later against a time nobody remembers.
DRAFT_TTL: Final = timedelta(hours=6)

# Public command registry used by the in-app Help drift gate. Commands must be in
# this set to reach dispatch below; adding one therefore requires documenting it.
SUPPORTED_TELEGRAM_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "dose",
        "edit",
        "episode",
        "help",
        "injection",
        "location",
        "privacy",
        "start",
        "symptom",
        "today",
        "undo",
    }
)

HELP_TEXT: Final = """\
HealthCurve bot

Just describe what happened and I'll show you a draft to confirm.
Example: "Took 15mg hydrocortisone at 7:08, slept badly, mild nausea"

Nothing is recorded until you confirm it.

Commands (these always work, even if the language model is offline):
/dose <amount> <medication> [HH:MM] - record a dose
/symptom <name> [0-10] - record a symptom
/injection <amount> - log an emergency injection
/episode start <trigger> - open a stress episode
/episode end - close the open episode
/today - what's recorded today vs your plan
/location - add optional coarse location to the pending draft
/edit <number> <field> <value> - correct amount, unit, time, or medication
/undo - cancel the pending draft
/privacy - what this bot stores
/help - this message
"""

PRIVACY_TEXT: Final = """\
What this bot stores

- Your message text is kept only while a draft is waiting for you. Once you confirm
  or cancel, the raw text is deleted and only the structured record remains.
- Telegram itself keeps your chat history. That is outside HealthCurve's control.
  Clear the chat there if that matters to you.
- Message text is sent to a language model running on private infrastructure. It is
  not sent to any third-party AI service.
- Phone location is optional and requires pressing Telegram's share button. Exact
  coordinates are rounded to 0.1 degrees before storage; exact coordinates are never
  written to HealthCurve's database or backups.
- The bot only responds to this chat. Messages from anywhere else are dropped.
- Nothing the model produces is recorded until you confirm it.
"""


class Reply:
    """What to send back. Returned rather than sent, so handlers stay testable."""

    __slots__ = ("draft_id", "reply_markup", "text")

    def __init__(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        draft_id: uuid.UUID | None = None,
    ) -> None:
        self.text = text
        self.reply_markup = reply_markup
        self.draft_id = draft_id


def handle_message(
    session: Session,
    owner: Owner,
    *,
    text: str,
    message_id: str | None = None,
    client: OllamaClient | None = None,
    limiter: RateLimiter | None = None,
    model_policy: RateLimitPolicy | None = None,
    now: datetime | None = None,
) -> Reply:
    """Entry point for an inbound text message."""
    now = now or datetime.now(UTC)
    text = text.strip()
    if not text:
        return Reply("I didn't get any text. Try /help.")

    if text.startswith("/"):
        return _handle_command(session, owner, text, now=now)

    return _handle_free_text(
        session,
        owner,
        text,
        message_id=message_id,
        client=client,
        limiter=limiter,
        model_policy=model_policy,
        now=now,
    )


# ---------------------------------------------------------------------------
# Commands (never touch the model)
# ---------------------------------------------------------------------------


def _handle_command(session: Session, owner: Owner, text: str, *, now: datetime) -> Reply:
    parts = text.split()
    command = parts[0].lower().lstrip("/").split("@")[0]
    args = parts[1:]

    if command not in SUPPORTED_TELEGRAM_COMMANDS:
        return Reply(f"Unknown command /{command}. Try /help.")

    match command:
        case "help" | "start":
            return Reply(HELP_TEXT)
        case "privacy":
            return Reply(PRIVACY_TEXT)
        case "dose":
            return _cmd_dose(session, owner, args, now=now)
        case "symptom":
            return _cmd_symptom(session, owner, args, now=now)
        case "injection":
            return _cmd_injection(session, owner, args, now=now)
        case "episode":
            return _cmd_episode(session, owner, args, now=now)
        case "today":
            return _cmd_today(session, owner, now=now)
        case "location":
            return Reply("Use the Add location button on your pending draft.")
        case "edit":
            return _cmd_edit(session, owner, args, now=now)
        case "undo":
            return _cmd_undo(session, owner)
        case _:  # pragma: no cover - registry and dispatch are checked together
            raise AssertionError(f"registered Telegram command is not dispatched: {command}")


def _cmd_dose(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    """``/dose 15 hydrocortisone [07:08]`` -- deterministic, no model involved.

    Still confirmed rather than written immediately: SAFE-11 applies to every path,
    and a typo in a command is as harmful as a misparse.
    """
    if len(args) < 2:
        return Reply("Usage: /dose <amount> <medication> [HH:MM]\nExample: /dose 15 hydrocortisone")

    try:
        amount = Decimal(args[0])
    except (InvalidOperation, ValueError):
        return Reply(f"I couldn't read '{args[0]}' as an amount.")
    if amount <= 0 or amount > 500:
        return Reply("That amount looks wrong. Enter it in mg, e.g. /dose 15 hydrocortisone")

    time_token = args[-1] if _looks_like_time(args[-1]) else None
    name_parts = args[1:-1] if time_token else args[1:]
    name = " ".join(name_parts)

    medication = meds.find_medication_by_name(session, owner.id, name)
    if medication is None:
        known = _known_medication_names(session, owner.id)
        return Reply(f"I don't know '{name}'. Your medications are: {known or 'none recorded yet'}")

    local = _local_now(owner, now)
    if time_token:
        parsed = _parse_time_token(time_token, local)
        if parsed is None:
            return Reply(f"I couldn't read '{time_token}' as a time. Use HH:MM.")
        local = parsed

    candidate = ValidatedCandidate(
        type=CandidateType.DOSE,
        medication_id=medication.id,
        medication_name=medication.name,
        amount=amount,
        unit=medication.default_unit.value,
        route=medication.default_route.value,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_symptom(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    if not args:
        return Reply("Usage: /symptom <name> [severity 0-10]\nExample: /symptom nausea 4")

    severity: int | None = None
    if args[-1].isdigit() and 0 <= int(args[-1]) <= 10:
        severity = int(args[-1])
        args = args[:-1]

    name = " ".join(args)
    if not name:
        return Reply("Tell me the symptom, e.g. /symptom fatigue 6")

    candidate = ValidatedCandidate(
        type=CandidateType.SYMPTOM,
        symptom_name=name,
        severity=severity,
        local_time=_local_now(owner, now),
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_injection(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    """Emergency injection. Recorded immediately -- no confirmation round trip.

    SAFE-23 requires this to be fast. A confirmation step during a crisis is the wrong
    trade; the record can be corrected afterwards (SAFE-08).
    """
    medication = session.scalar(
        select(Medication).where(
            Medication.owner_id == owner.id,
            Medication.default_route == Route.INTRAMUSCULAR,
        )
    )
    if medication is None:
        return Reply(
            "No injectable medication is recorded, so I can't log this. "
            "Add one in the web app first. If this is an emergency, call your local "
            "emergency services now."
        )

    amount = Decimal(100)
    if args:
        try:
            amount = Decimal(args[0])
        except (InvalidOperation, ValueError):
            return Reply(f"I couldn't read '{args[0]}' as an amount. Usage: /injection 100")

    injection = events.create_event(
        session,
        EmergencyInjectionEvent,
        owner_id=owner.id,
        event_time=from_instant(now, owner.default_timezone),
        source_type=SourceType.TELEGRAM,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=medication.id,
        amount=amount,
        unit=medication.default_unit.value,
        route=Route.INTRAMUSCULAR.value,
        reason="logged via Telegram /injection",
    )
    return Reply(
        f"Logged: emergency injection {amount} {medication.default_unit.value} "
        f"{medication.name} at {injection.local_time:%H:%M}.\n\n"
        "If you have not already done so, call your local emergency services."
    )


def _cmd_episode(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    if not args:
        return Reply("Usage: /episode start <trigger>  |  /episode end")

    action = args[0].lower()
    open_episode = session.scalar(
        select(StressEpisode).where(
            StressEpisode.owner_id == owner.id, StressEpisode.status == EpisodeStatus.OPEN
        )
    )

    if action == "start":
        if open_episode is not None:
            return Reply(
                f"An episode is already open (trigger: {open_episode.trigger}). "
                "Close it with /episode end first."
            )
        trigger = " ".join(args[1:]) or "unspecified"
        episode = StressEpisode(
            owner_id=owner.id,
            trigger=trigger,
            status=EpisodeStatus.OPEN,
            started_at=now,
            timezone=owner.default_timezone,
            recorded_at=now,
        )
        session.add(episode)
        session.flush()
        return Reply(f"Episode opened: {trigger}. Doses you log now will be linked to it.")

    if action == "end":
        if open_episode is None:
            return Reply("No episode is open.")
        open_episode.status = EpisodeStatus.RESOLVED
        open_episode.ended_at = now
        duration = now - open_episode.started_at
        hours = int(duration.total_seconds() // 3600)
        return Reply(f"Episode closed after about {hours} hour(s): {open_episode.trigger}.")

    return Reply("Usage: /episode start <trigger>  |  /episode end")


def _cmd_today(session: Session, owner: Owner, *, now: datetime) -> Reply:
    """Today against the plan. Deterministic -- no model, no charts."""
    local_today = _local_now(owner, now).date()
    comparison = meds.compare_day(
        session, owner_id=owner.id, day=local_today, timezone=owner.default_timezone
    )

    lines = [f"Today ({local_today.isoformat()}, {owner.default_timezone})", ""]
    slots = comparison["slots"]
    if not slots:
        lines.append("Nothing recorded, and no approved plan for today.")
    for slot in slots:  # type: ignore[union-attr]
        if slot.status == "missing":
            lines.append(
                f"  [ ] {slot.scheduled_local_time:%H:%M}  {slot.medication_name} "
                f"{slot.planned_amount} - not recorded"
            )
        elif slot.status == "unplanned":
            lines.append(
                f"  [+] {slot.actual_local_time:%H:%M}  {slot.medication_name} "
                f"{slot.actual_amount} - extra"
            )
        else:
            mark = "x" if slot.status == "on_time" else "~"
            lines.append(
                f"  [{mark}] {slot.actual_local_time:%H:%M}  {slot.medication_name} "
                f"{slot.actual_amount} ({slot.status.replace('_', ' ')})"
            )

    lines.append("")
    planned = comparison["planned_total"]
    lines.append(
        f"Taken: {comparison['actual_total']}" + (f" of planned {planned}" if planned else "")
    )
    if comparison["missed_slots"]:
        # Phrased as "not recorded", not "missed": the record cannot tell the
        # difference between a skipped dose and one taken but not logged.
        lines.append(f"{comparison['missed_slots']} scheduled dose(s) not recorded.")
    return Reply("\n".join(lines))


def _cmd_undo(session: Session, owner: Owner) -> Reply:
    draft = _pending_draft(session, owner.id)
    if draft is None:
        return Reply("Nothing pending to undo.")
    draft.state = DraftState.CANCELLED
    draft.resolved_at = datetime.now(UTC)
    draft.purge_raw_text()
    location.cancel_for_draft(session, owner.id, draft.id)
    return Reply("Cancelled. Nothing was recorded.")


def _cmd_edit(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = "Usage: /edit <number> <amount|unit|time|medication> <value>"
    if len(args) < 3 or not args[0].isdigit():
        return Reply(usage)
    draft = _pending_draft(session, owner.id)
    if draft is None:
        return Reply("There is no pending draft to edit.")
    candidates = [ValidatedCandidate.model_validate(item) for item in draft.candidates]
    index = int(args[0]) - 1
    if index < 0 or index >= len(candidates):
        return Reply(f"That draft has {len(candidates)} item(s). {usage}")
    candidate = candidates[index]
    if candidate.type is not CandidateType.DOSE:
        return Reply("Only dose amount, unit, time, and medication can be edited here.")

    field = args[1].lower()
    value = " ".join(args[2:]).strip()
    flags = list(candidate.flags)
    changes: dict[str, object] = {}
    if field == "amount":
        try:
            amount = Decimal(value)
        except InvalidOperation:
            return Reply("I couldn't read that amount. Example: /edit 1 amount 15")
        if amount <= 0 or amount > 500:
            return Reply("That amount is outside the accepted range (greater than 0, at most 500).")
        changes["amount"] = amount
        _remove_flags(
            flags,
            FlagCode.MISSING_AMOUNT,
            FlagCode.UNPARSEABLE_AMOUNT,
            FlagCode.IMPLAUSIBLE_AMOUNT,
        )
    elif field == "unit":
        try:
            changes["unit"] = DoseUnit(value.lower()).value
        except ValueError:
            return Reply("Unit must be one of: mg, mcg, ml, tablet.")
        _remove_flags(flags, FlagCode.MISSING_UNIT)
    elif field == "medication":
        medication = meds.find_medication_by_name(session, owner.id, value)
        if medication is None:
            return Reply(f"I don't know '{value}'. Choose a medication already in your record.")
        changes.update(medication_id=medication.id, medication_name=medication.name)
        _remove_flags(flags, FlagCode.UNKNOWN_MEDICATION)
    elif field == "time":
        local = _parse_time_token(value, _local_now(owner, now))
        if local is None:
            return Reply("I couldn't read that time. Use 24-hour HH:MM, e.g. /edit 1 time 07:05")
        try:
            resolved = resolve_event_time(local, owner.default_timezone)
        except AmbiguousLocalTimeError:
            return Reply("That time happened twice when the clocks changed; use the web editor.")
        except NonExistentLocalTimeError:
            return Reply("That time did not exist when the clocks changed; choose another time.")
        if resolved.occurred_at > now + timedelta(minutes=10):
            return Reply("That time resolves into the future; choose the time the event happened.")
        changes["local_time"] = local
        _remove_flags(
            flags,
            FlagCode.MISSING_TIME,
            FlagCode.ASSUMED_TIME,
            FlagCode.UNPARSEABLE_TIME,
            FlagCode.AMBIGUOUS_TIME,
            FlagCode.NONEXISTENT_TIME,
            FlagCode.FUTURE_TIME,
        )
    else:
        return Reply(usage)

    changes["flags"] = flags
    changes["is_actionable"] = not bool(BLOCKING_FLAGS & set(flags))
    edited = ValidatedCandidate.model_validate({**candidate.model_dump(mode="python"), **changes})
    if draft.original_candidates is None:
        draft.original_candidates = [dict(item) for item in draft.candidates]
    candidates[index] = edited
    draft.candidates = [item.model_dump(mode="json") for item in candidates]
    draft.state = DraftState.EDITED
    return _draft_reply(draft, candidates, edited=True)


def _remove_flags(flags: list[FlagCode], *removed: FlagCode) -> None:
    blocked = set(removed)
    flags[:] = [flag for flag in flags if flag not in blocked]


# ---------------------------------------------------------------------------
# Free text -> draft
# ---------------------------------------------------------------------------


def _handle_free_text(
    session: Session,
    owner: Owner,
    text: str,
    *,
    message_id: str | None,
    client: OllamaClient | None,
    limiter: RateLimiter | None,
    model_policy: RateLimitPolicy | None,
    now: datetime,
) -> Reply:
    if limiter is not None and model_policy is not None:
        try:
            limiter.check("model", str(owner.id), model_policy)
        except RateLimitExceeded as exc:
            return Reply(
                "You've reached the automatic-reading limit. Nothing was recorded. "
                f"Try again in about {exc.result.retry_after} seconds, or use /dose, "
                "/symptom, or /injection now."
            )
        except RateLimitUnavailable:
            return Reply(
                "I can't safely check the automatic-reading limit right now. Nothing "
                "was recorded. You can still use /dose, /symptom, or /injection."
            )
    result = extract(
        session,
        owner_id=owner.id,
        message=text,
        timezone=owner.default_timezone,
        now=now,
        client=client,
    )

    if result.outcome is not ModelOutcome.OK:
        # SAFE-21 / ADR-0003: degrade to the deterministic commands, never guess.
        return Reply(
            "I can't read that automatically right now (the language model is "
            "unavailable). Nothing was recorded.\n\n"
            "You can still use commands:\n"
            "/dose 15 hydrocortisone\n"
            "/symptom nausea 4\n"
            "/today"
        )

    if not result.candidates:
        return Reply(
            "I couldn't find anything to record in that. Nothing was saved.\n"
            "Try something like: Took 15mg hydrocortisone at 7:08"
        )

    # SAFE-15 / SAFE-16: this is the one write that carries model output, so it goes
    # through the restricted role. Even a prompt injection that survives validation
    # reaches a connection with no INSERT on fact or plan. The deterministic command
    # paths above are not model output and keep the caller's session.
    with get_ai_session_factory()() as ai_session, ai_session.begin():
        draft = _store_draft(
            ai_session,
            owner,
            result.candidates,
            raw_text=text,
            source="telegram",
            message_id=message_id,
            model_name=result.model_name,
            model_digest=result.model_digest,
        )
    return _draft_reply(draft, result.candidates)


def _store_draft(
    session: Session,
    owner: Owner,
    candidates: list[ValidatedCandidate],
    *,
    raw_text: str | None,
    source: str,
    message_id: str | None = None,
    model_name: str | None = None,
    model_digest: str | None = None,
) -> ExtractionDraft:
    """Persist candidates as a pending draft. Supersedes any earlier pending draft."""
    existing = _pending_draft(session, owner.id)
    if existing is not None:
        existing.state = DraftState.CANCELLED
        existing.resolved_at = datetime.now(UTC)
        existing.purge_raw_text()

    from healthcurve.ai.extraction import PROMPT_VERSION, SCHEMA_VERSION

    draft = ExtractionDraft(
        owner_id=owner.id,
        source=source,
        provider_message_id=message_id,
        raw_text=raw_text,
        candidates=[c.model_dump(mode="json") for c in candidates],
        state=DraftState.PENDING,
        model_name=model_name,
        model_digest=model_digest,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    session.add(draft)
    session.flush()
    return draft


_FLAG_EXPLANATIONS: Final[dict[FlagCode, str]] = {
    FlagCode.NEGATED: "this reads like a dose you did NOT take",
    FlagCode.HYPOTHETICAL: "this reads like a question, not something that happened",
    FlagCode.UNKNOWN_MEDICATION: "I don't recognise that medication",
    FlagCode.MISSING_AMOUNT: "no amount given",
    FlagCode.MISSING_UNIT: "no unit given",
    FlagCode.UNPARSEABLE_AMOUNT: "I couldn't read the amount",
    FlagCode.IMPLAUSIBLE_AMOUNT: "that amount looks too large",
    FlagCode.MISSING_TIME: "no time given",
    FlagCode.ASSUMED_TIME: "you didn't give a time, so I've used when you sent this",
    FlagCode.UNPARSEABLE_TIME: "I couldn't read the time",
    FlagCode.AMBIGUOUS_TIME: "that time happened twice (clocks went back) - which one?",
    FlagCode.NONEXISTENT_TIME: "that time doesn't exist (clocks went forward)",
    FlagCode.FUTURE_TIME: "that time is in the future",
    FlagCode.POSSIBLE_DUPLICATE: "there's already a similar dose near that time",
    FlagCode.LOW_CONFIDENCE: "I'm not confident about this one",
    FlagCode.PROMPT_INJECTION_SUSPECTED: "this message contains text aimed at the parser",
}


def _draft_reply(
    draft: ExtractionDraft, candidates: list[ValidatedCandidate], *, edited: bool = False
) -> Reply:
    lines = ["Edited draft:" if edited else "I read this as:", ""]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(f"{index}. {_describe(candidate)}")
        for flag in candidate.flags:
            lines.append(f"     ! {_FLAG_EXPLANATIONS.get(flag, flag.value)}")

    blocked = [c for c in candidates if not c.is_actionable]
    lines.append("")
    if blocked:
        lines.append(
            "Some of these need fixing before I can record them. "
            "Use /edit <number> <field> <value>, or /undo to cancel."
        )
    else:
        lines.append("Nothing is recorded yet. Confirm to save it.")

    keyboard: dict[str, Any] = {
        "inline_keyboard": [
            [
                {"text": "Confirm", "callback_data": f"confirm:{draft.id}"},
                {"text": "Edit", "callback_data": f"edit:{draft.id}"},
                {"text": "Cancel", "callback_data": f"cancel:{draft.id}"},
            ],
            [{"text": "Add location (optional)", "callback_data": f"location:{draft.id}"}],
        ]
    }
    if blocked:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Edit", "callback_data": f"edit:{draft.id}"},
                    {"text": "Cancel", "callback_data": f"cancel:{draft.id}"},
                ],
                [
                    {
                        "text": "Add location (optional)",
                        "callback_data": f"location:{draft.id}",
                    }
                ],
            ]
        }

    return Reply("\n".join(lines), reply_markup=keyboard, draft_id=draft.id)


def start_location_request(
    session: Session, owner: Owner, draft_id: uuid.UUID, *, chat_id: int
) -> Reply:
    request = location.begin_request(session, owner, chat_id=chat_id, draft_id=draft_id)
    if request is None:
        return Reply("That draft is no longer waiting for a location.")
    return Reply(
        "Location is optional. Choose one within 10 minutes. Telegram will ask before "
        "sharing your phone location; HealthCurve rounds it before storage.",
        reply_markup={
            "keyboard": [
                [{"text": "Share current location", "request_location": True}],
                [{"text": "Use saved Home area"}, {"text": "No location"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
            "input_field_placeholder": "Choose a location option",
        },
        draft_id=draft_id,
    )


def handle_phone_location(
    session: Session,
    owner: Owner,
    *,
    chat_id: int,
    latitude: object,
    longitude: object,
) -> Reply:
    result = location.attach_phone_location(
        session, owner, chat_id=chat_id, latitude=latitude, longitude=longitude
    )
    if result is location.LocationResult.ATTACHED:
        draft = _pending_draft(session, owner.id)
        keyboard = None
        if draft is not None:
            keyboard = {
                "inline_keyboard": [
                    [{"text": "Save as Home area", "callback_data": f"save_home:{draft.id}"}]
                ]
            }
        return Reply(
            "Coarse location added to the pending draft. Exact coordinates were not stored. "
            "Confirm the original draft to record it.",
            reply_markup=keyboard,
            draft_id=draft.id if draft is not None else None,
        )
    if result is location.LocationResult.INVALID:
        return Reply("Telegram sent an invalid location. Nothing was stored; try again.")
    return Reply("That location request expired or was cancelled. Nothing was stored.")


def use_saved_home(session: Session, owner: Owner, *, chat_id: int) -> Reply:
    result = location.attach_saved_home(session, owner, chat_id=chat_id)
    if result is location.LocationResult.ATTACHED:
        return Reply(
            "Saved Home area added to the pending draft. Confirm the original draft to record it.",
            reply_markup={"remove_keyboard": True},
        )
    if result is location.LocationResult.NO_HOME:
        return Reply(
            "No Home area is saved yet. Share a current location, then choose Save as Home area."
        )
    return Reply("That location request expired or was cancelled. Nothing was stored.")


def decline_location(session: Session, owner: Owner, *, chat_id: int) -> Reply:
    location.cancel_request(session, owner, chat_id=chat_id)
    return Reply("No location will be attached.", reply_markup={"remove_keyboard": True})


def save_location_as_home(session: Session, owner: Owner, draft_id: uuid.UUID) -> Reply:
    if location.save_attached_as_home(session, owner, draft_id=draft_id):
        return Reply("Saved as your coarse Home area. Exact coordinates were never stored.")
    return Reply("That location is no longer available to save.")


def draft_edit_help(session: Session, owner: Owner, draft_id: uuid.UUID) -> Reply:
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None or draft.owner_id != owner.id or not draft.is_pending:
        return Reply("I can't find an editable pending draft.")
    candidates = [ValidatedCandidate.model_validate(item) for item in draft.candidates]
    reply = _draft_reply(draft, candidates, edited=draft.state is DraftState.EDITED)
    reply.text += (
        "\n\nCorrect one field with:\n"
        "/edit <number> <amount|unit|time|medication> <value>\n"
        "Example: /edit 1 amount 15"
    )
    return reply


def _describe(candidate: ValidatedCandidate) -> str:
    when = candidate.local_time.strftime("%H:%M") if candidate.local_time else "time unknown"
    match candidate.type:
        case CandidateType.DOSE:
            amount = f"{candidate.amount} {candidate.unit or ''}".strip()
            return f"Dose: {amount} {candidate.medication_name or '?'} at {when}"
        case CandidateType.SYMPTOM:
            severity = (
                f" (severity {candidate.severity}/10)" if candidate.severity is not None else ""
            )
            return f"Symptom: {candidate.symptom_name}{severity} at {when}"
        case CandidateType.DIARY:
            return f"Note: {(candidate.text or '')[:100]}"
        case CandidateType.LIFE_EVENT:
            return f"Life event: {(candidate.text or '')[:100]} at {when}"
        case _:  # pragma: no cover
            return str(candidate.type)


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def confirm_draft(session: Session, owner: Owner, draft_id: uuid.UUID) -> Reply:
    """Turn a pending draft into recorded facts. The only path from draft to fact."""
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None or draft.owner_id != owner.id:
        return Reply("I can't find that draft.")
    if not draft.is_pending:
        return Reply("That draft was already resolved.")

    candidates = [ValidatedCandidate.model_validate(c) for c in draft.candidates]
    blocked = [c for c in candidates if not c.is_actionable]
    if blocked:
        return Reply("Some entries still need fixing, so I haven't recorded anything.")

    created: list[str] = []
    summaries: list[str] = []

    for candidate in candidates:
        event = _persist(session, owner, candidate)
        if event is None:
            continue
        created.append(str(event.id))
        summaries.append(_describe(candidate))

    context = location.consume_for_confirm(session, owner, draft_id=draft.id)
    if context is not None:
        created.append(str(context.id))
        summaries.append("Coarse location context (rounded to 0.1 degrees)")

    draft.state = (
        DraftState.EDITED if draft.original_candidates is not None else DraftState.CONFIRMED
    )
    draft.resolved_at = datetime.now(UTC)
    draft.created_event_ids = created
    # The structured fact now exists, so the verbatim message is no longer needed (C9).
    draft.purge_raw_text()

    if not created:
        return Reply("Nothing was recorded.")
    return Reply("Recorded:\n" + "\n".join(f"  {s}" for s in summaries))


def _persist(session: Session, owner: Owner, candidate: ValidatedCandidate) -> Any | None:
    """Write one confirmed candidate as a fact."""
    if candidate.local_time is None:
        # Never invent a time here. Substituting the confirmation moment would record
        # something the owner was never shown -- a dose stamped 21:00 because that is
        # when they pressed Confirm on "took my morning dose". Validation proposes a
        # time and flags it; if it could not, this candidate is not recordable.
        return None
    local = candidate.local_time
    try:
        event_time = resolve_event_time(local, candidate.timezone)
    except Exception:
        return None

    open_episode = session.scalar(
        select(StressEpisode).where(
            StressEpisode.owner_id == owner.id, StressEpisode.status == EpisodeStatus.OPEN
        )
    )

    match candidate.type:
        case CandidateType.DOSE:
            if candidate.medication_id is None or candidate.amount is None:
                return None
            version = meds.active_version_at(session, owner.id, event_time.occurred_at)
            return events.create_event(
                session,
                DoseEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                medication_id=candidate.medication_id,
                amount=candidate.amount,
                unit=candidate.unit or "mg",
                route=candidate.route or Route.ORAL.value,
                category=DoseCategory.STRESS if open_episode else DoseCategory.SCHEDULED,
                regimen_version_id=version.id if version else None,
                episode_id=open_episode.id if open_episode else None,
            )
        case CandidateType.SYMPTOM:
            return events.create_event(
                session,
                SymptomEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                name=candidate.symptom_name or "unspecified",
                severity=candidate.severity,
                episode_id=open_episode.id if open_episode else None,
            )
        case CandidateType.DIARY | CandidateType.LIFE_EVENT:
            return events.create_event(
                session,
                DiaryEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                text=candidate.text or "",
            )
        case _:  # pragma: no cover
            return None


def cancel_draft(session: Session, owner: Owner, draft_id: uuid.UUID) -> Reply:
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None or draft.owner_id != owner.id:
        return Reply("I can't find that draft.")
    if draft.is_pending:
        draft.state = DraftState.CANCELLED
        draft.resolved_at = datetime.now(UTC)
        draft.purge_raw_text()
        location.cancel_for_draft(session, owner.id, draft.id)
    return Reply("Cancelled. Nothing was recorded.")


def expire_stale_drafts(session: Session, *, now: datetime | None = None) -> int:
    """Purge drafts nobody answered. Keeps C9 message text from lingering."""
    now = now or datetime.now(UTC)
    cutoff = now - DRAFT_TTL
    stale = session.scalars(
        select(ExtractionDraft).where(
            ExtractionDraft.state.in_((DraftState.PENDING, DraftState.EDITED)),
            ExtractionDraft.resolved_at.is_(None),
            ExtractionDraft.created_at < cutoff,
        )
    ).all()
    for draft in stale:
        draft.state = DraftState.EXPIRED
        draft.resolved_at = now
        draft.purge_raw_text()
        location.cancel_for_draft(session, draft.owner_id, draft.id, now=now)
    location.expire_requests(session, now=now)
    return len(stale)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pending_draft(session: Session, owner_id: uuid.UUID) -> ExtractionDraft | None:
    return session.scalar(
        select(ExtractionDraft)
        .where(
            ExtractionDraft.owner_id == owner_id,
            ExtractionDraft.source.in_(("telegram", "telegram_command")),
            ExtractionDraft.state.in_((DraftState.PENDING, DraftState.EDITED)),
            ExtractionDraft.resolved_at.is_(None),
        )
        .order_by(ExtractionDraft.created_at.desc())
        .limit(1)
    )


def _known_medication_names(session: Session, owner_id: uuid.UUID) -> str:
    names = session.scalars(
        select(Medication.name).where(Medication.owner_id == owner_id).order_by(Medication.name)
    ).all()
    return ", ".join(names)


def _local_now(owner: Owner, now: datetime) -> datetime:
    return from_instant(now, owner.default_timezone).local_time


def _looks_like_time(token: str) -> bool:
    return ":" in token and len(token) <= 5


def _parse_time_token(token: str, local_reference: datetime) -> datetime | None:
    """Parse ``HH:MM`` against today, rolling back a day if that would be the future."""
    try:
        hour_text, minute_text = token.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, TypeError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    candidate = local_reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > local_reference + timedelta(minutes=10):
        # "at 23:30" sent at 00:10 means last night, not tonight.
        candidate -= timedelta(days=1)
    return candidate


def today_local(owner: Owner, now: datetime) -> date:
    return _local_now(owner, now).date()
