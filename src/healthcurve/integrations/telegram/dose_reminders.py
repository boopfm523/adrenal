"""Durable Telegram reminders for plan slots that still appear unrecorded."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from healthcurve.ai.extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    CandidateType,
    ValidatedCandidate,
)
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.events.timekeeping import (
    NonExistentLocalTimeError,
    from_instant,
    resolve_event_time,
)
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.models import DoseReminderState, TelegramDoseReminder
from healthcurve.logging import get_logger
from healthcurve.medications import service as medications
from healthcurve.medications.models import (
    DoseCategory,
    DoseTimingMode,
    RegimenDoseSlot,
    RegimenStatus,
    RegimenVersion,
)
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

REMINDER_DELAY: Final = timedelta(minutes=30)
MAX_REMINDER_AGE: Final = timedelta(hours=6)
SNOOZE_DELAY: Final = timedelta(minutes=30)
DOSE_REMINDER_TASK: Final = "telegram.dose_reminders.check"
log = get_logger(__name__)


def schedule_reminder_check(session: Session, now: datetime) -> Job:
    """Ensure one reminder check exists for each UTC minute across restarts."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise JobQueueError("dose_reminder_schedule_invalid")
    bucket = now.astimezone(UTC).replace(second=0, microsecond=0)
    key = bucket.isoformat().replace("+00:00", "Z")
    return enqueue(
        session,
        task=DOSE_REMINDER_TASK,
        payload={"scheduled_at_utc": key},
        idempotency_key=f"minute:{key}",
        run_at=bucket,
        priority=40,
        max_attempts=4,
    )


def schedule_due_reminders(session: Session, now: datetime) -> int:
    """Insert each due slot occurrence once; returns newly scheduled count."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise JobQueueError("dose_reminder_schedule_invalid")
    count = 0
    for owner in session.scalars(select(Owner).order_by(Owner.id)):
        plan_timezones = {
            timezone
            for timezone in session.scalars(
                select(RegimenVersion.effective_timezone).where(
                    RegimenVersion.owner_id == owner.id,
                    RegimenVersion.status == RegimenStatus.APPROVED,
                    RegimenVersion.effective_timezone.is_not(None),
                )
            )
            if timezone is not None
        }
        plan_timezones.add(owner.default_timezone)
        for timezone in sorted(plan_timezones):
            zone = ZoneInfo(timezone)
            local_now = now.astimezone(zone)
            for local_day in {local_now.date(), local_now.date() - timedelta(days=1)}:
                count += _schedule_day(session, owner, local_day, timezone, now)
    return count


def _schedule_day(
    session: Session, owner: Owner, local_day: date, timezone: str, now: datetime
) -> int:
    count = 0
    comparison = medications.compare_day(
        session, owner_id=owner.id, day=local_day, timezone=timezone
    )
    slots = cast(list[medications.SlotComparison], comparison["slots"])
    for item in slots:
        if item.status != "missing" or item.slot_id is None:
            continue
        if item.regimen_version_id is None:
            continue
        version = session.get(RegimenVersion, item.regimen_version_id)
        if version is None:
            continue
        plan_timezone = version.effective_timezone or owner.default_timezone
        if plan_timezone != timezone:
            continue
        local_threshold = (
            item.reminder_local_time
            if item.timing_mode is DoseTimingMode.WAKE
            else item.scheduled_local_time
        )
        if not isinstance(local_threshold, time):
            continue
        try:
            scheduled = resolve_event_time(
                datetime.combine(local_day, local_threshold),
                plan_timezone,
                fold=0,
            ).occurred_at
        except NonExistentLocalTimeError:
            log.warning(
                "scheduled dose reminder skipped",
                task=DOSE_REMINDER_TASK,
                reason_code="nonexistent_plan_local_time",
            )
            continue
        due = scheduled if item.timing_mode is DoseTimingMode.WAKE else scheduled + REMINDER_DELAY
        now_utc = now.astimezone(UTC)
        if due > now_utc or now_utc - scheduled > MAX_REMINDER_AGE:
            continue
        statement = (
            insert(TelegramDoseReminder)
            .values(
                owner_id=owner.id,
                regimen_version_id=version.id,
                slot_id=item.slot_id,
                local_date=local_day,
                scheduled_at=scheduled,
                due_at=due,
                state=DoseReminderState.PENDING,
            )
            .on_conflict_do_nothing(constraint="uq_telegram_dose_reminder_occurrence")
            .returning(TelegramDoseReminder.id)
        )
        if session.scalar(statement) is not None:
            count += 1
    return count


def send_due_reminders(
    session: Session,
    *,
    client: TelegramClient,
    chat_id: int,
    now: datetime,
) -> int:
    """Send pending/snoozed due reminders; satisfied slots are closed silently."""
    sent = 0
    due = list(
        session.scalars(
            select(TelegramDoseReminder)
            .where(
                TelegramDoseReminder.state.in_(
                    (DoseReminderState.PENDING, DoseReminderState.SNOOZED)
                ),
                TelegramDoseReminder.due_at <= now.astimezone(UTC),
            )
            .order_by(TelegramDoseReminder.due_at, TelegramDoseReminder.id)
            .with_for_update(skip_locked=True)
        )
    )
    for reminder in due:
        owner = session.get(Owner, reminder.owner_id)
        slot = session.get(RegimenDoseSlot, reminder.slot_id)
        if owner is None or slot is None or _slot_is_recorded(session, reminder, owner):
            reminder.state = DoseReminderState.SATISFIED
            reminder.resolved_at = now
            continue
        version = session.get(RegimenVersion, reminder.regimen_version_id)
        timezone = (
            version.effective_timezone
            if version is not None and version.effective_timezone
            else owner.default_timezone
        )
        scheduled = from_instant(reminder.scheduled_at, timezone).local_time
        if slot.timing_mode is DoseTimingMode.WAKE:
            message = (
                f"When-you-wake dose appears unrecorded: {slot.medication.name} "
                f"{slot.amount} {slot.unit.value}. Reminder threshold: {scheduled:%H:%M}.\n"
                "This is a record-completeness reminder, not advice to take medication."
            )
        else:
            message = (
                f"Scheduled dose appears unrecorded: {slot.medication.name} "
                f"{slot.amount} {slot.unit.value} at {scheduled:%H:%M}.\n"
                "This is a record-completeness reminder, not advice to take medication."
            )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Record taken", "callback_data": f"reminder_record:{reminder.id}"},
                    {"text": "Snooze 30 min", "callback_data": f"reminder_snooze:{reminder.id}"},
                    {"text": "Dismiss", "callback_data": f"reminder_dismiss:{reminder.id}"},
                ]
            ]
        }
        if not client.send_message(chat_id, message, reply_markup=keyboard):
            raise JobQueueError("telegram_dose_reminder_send_failed")
        reminder.state = DoseReminderState.SENT
        reminder.sent_at = now
        sent += 1
    if due:
        log.info(
            "scheduled dose reminder check completed",
            task=DOSE_REMINDER_TASK,
            outcome="completed",
            due_count=len(due),
            sent_count=sent,
            satisfied_count=len(due) - sent,
        )
    return sent


def make_reminder_handler(
    *,
    client: TelegramClient,
    chat_id: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JobHandler:
    def handle(session: Session, _payload: Mapping[str, object]) -> None:
        now = clock()
        scheduled = schedule_due_reminders(session, now)
        send_due_reminders(session, client=client, chat_id=chat_id, now=now)
        if scheduled:
            log.info(
                "scheduled dose reminders created",
                task=DOSE_REMINDER_TASK,
                outcome="scheduled",
                reminder_count=scheduled,
            )

    return handle


def handle_action(
    session: Session, owner: Owner, reminder_id: uuid.UUID, action: str, *, now: datetime
) -> tuple[str, dict[str, object] | None]:
    reminder = session.get(TelegramDoseReminder, reminder_id)
    if reminder is None or reminder.owner_id != owner.id:
        return "That reminder is no longer available.", None
    if reminder.state in (DoseReminderState.DISMISSED, DoseReminderState.SATISFIED):
        return "That reminder was already resolved.", None
    if action == "dismiss":
        reminder.state = DoseReminderState.DISMISSED
        reminder.resolved_at = now
        return "Reminder dismissed. No health fact was changed.", None
    if action == "snooze":
        reminder.state = DoseReminderState.SNOOZED
        reminder.due_at = now + SNOOZE_DELAY
        return "Reminder snoozed for 30 minutes. No health fact was changed.", None
    if action != "record":
        return "Unknown reminder action.", None
    if reminder.draft_id is not None:
        return "A confirmation draft already exists for this reminder.", None
    slot = session.get(RegimenDoseSlot, reminder.slot_id)
    if slot is None:
        return "The referenced plan slot is no longer available.", None
    captured = from_instant(now, owner.default_timezone)
    candidate = ValidatedCandidate(
        type=CandidateType.DOSE,
        medication_id=slot.medication_id,
        medication_name=slot.medication.name,
        amount=slot.amount,
        unit=slot.unit.value,
        route=slot.route.value,
        dose_category=DoseCategory.SCHEDULED,
        local_time=captured.local_time,
        timezone=captured.timezone,
        confidence=1.0,
        flags=[],
    )
    draft = ExtractionDraft(
        owner_id=owner.id,
        source="telegram_reminder",
        provider_message_id=f"dose-reminder:{reminder.id}",
        raw_text=None,
        candidates=[candidate.model_dump(mode="json")],
        original_candidates=[candidate.model_dump(mode="json")],
        state=DraftState.PENDING,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    session.add(draft)
    session.flush()
    reminder.state = DoseReminderState.RECORD_PENDING
    reminder.draft_id = draft.id
    keyboard: dict[str, object] = {
        "inline_keyboard": [
            [
                {"text": "Confirm", "callback_data": f"confirm:{draft.id}"},
                {"text": "Edit", "callback_data": f"edit:{draft.id}"},
                {"text": "Cancel", "callback_data": f"cancel:{draft.id}"},
            ]
        ]
    }
    return (
        f"Confirm regular dose: {slot.medication.name} {slot.amount} {slot.unit.value} "
        f"at {captured.local_time:%H:%M}. Nothing is recorded until you confirm.",
        keyboard,
    )


def _slot_is_recorded(session: Session, reminder: TelegramDoseReminder, owner: Owner) -> bool:
    version = session.get(RegimenVersion, reminder.regimen_version_id)
    timezone = (
        version.effective_timezone
        if version is not None and version.effective_timezone
        else owner.default_timezone
    )
    result = medications.compare_day(
        session,
        owner_id=owner.id,
        day=reminder.local_date,
        timezone=timezone,
    )
    slots = cast(list[medications.SlotComparison], result["slots"])
    return any(item.slot_id == reminder.slot_id and item.status != "missing" for item in slots)
