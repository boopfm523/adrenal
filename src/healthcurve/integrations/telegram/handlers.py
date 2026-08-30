"""Telegram message handling: commands, extraction drafts, confirmation.

The flow for free text is: receive -> extract -> validate -> show draft -> confirm,
edit, or cancel -> store -> reply with what was recorded. Nothing becomes a fact
before the confirm step (SAFE-11, SAFE-12).

Deterministic health-recording and Beads-read slash commands keep the bot useful when
the model is down (ADR-0003). They never touch the model. ``/bd-add`` is the explicit
product-request exception: it fails closed unless the local model produces a validated
proposal. Natural-language project requests pass through a separate fixed intent enum.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any, Final, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.ai.extraction import (
    BLOCKING_FLAGS,
    CandidateType,
    FlagCode,
    ValidatedCandidate,
    explicit_body_position,
    explicit_measurement_setting,
    extract,
    extract_deterministically,
    find_explicit_weight,
    find_time_expression,
    looks_like_deterministic_health_entry,
    normalise_local_time,
)
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.ai.ollama import ModelOutcome, OllamaClient
from healthcurve.config import get_settings
from healthcurve.db import get_ai_session_factory
from healthcurve.episodes.models import EmergencyInjectionEvent, EpisodeStatus, StressEpisode
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import (
    SYMPTOM_TRACKING_CATEGORY_REVISION,
    DiaryEvent,
    LifeEvent,
    LifeEventCategory,
    MealEvent,
    MealSize,
    SymptomEvent,
    SymptomTrackingCategory,
)
from healthcurve.events.timekeeping import (
    AmbiguousLocalTimeError,
    NonExistentLocalTimeError,
    from_instant,
    is_ambiguous,
    is_nonexistent,
    resolve_event_time,
    timezone_abbreviation,
)
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import conversation, location
from healthcurve.integrations.telegram.beads_operations import (
    BeadsOperation,
    classify_beads_intent,
    looks_like_beads_request,
    queue_operation,
    queued_operation,
)
from healthcurve.integrations.telegram.feature_requests import (
    FeatureRequestEvaluationFailed,
    FeatureRequestNeedsClarification,
    FeatureRequestRejected,
    evaluate_request,
    queue_request,
    queued_request,
    validate_clarification_answer,
    validate_request,
)
from healthcurve.integrations.telegram.models import DoseReminderState, TelegramDoseReminder
from healthcurve.logging import get_logger
from healthcurve.medications import service as meds
from healthcurve.medications.models import (
    DoseCategory,
    DoseEvent,
    DoseTimingMode,
    DoseUnit,
    Medication,
    RegimenDoseSlot,
    Route,
)
from healthcurve.operations.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitUnavailable,
)
from healthcurve.vitals import service as vitals
from healthcurve.vitals.models import (
    BloodPressureEvent,
    BodyPosition,
    MeasurementSetting,
    TemperatureEvent,
    TemperatureUnit,
    WeightEvent,
    WeightUnit,
)

log = get_logger(__name__)

#: A draft the owner never answers is purged rather than left to be confirmed days
#: later against a time nobody remembers.
DRAFT_TTL: Final = timedelta(hours=6)

_EPISODE_START_PHRASE: Final = re.compile(
    r"^(?:an?\s+)?episode\s+(?:is\s+)?(?:start(?:ing|ed)?|beg(?:in|inning|an))"
    r"(?:\s*(?::|-|because of|due to)\s*(?P<trigger>.+))?[.!]?$",
    re.IGNORECASE,
)
_EPISODE_END_PHRASE: Final = re.compile(
    r"^(?:the\s+)?episode\s+(?:is\s+)?(?:end(?:ing|ed)?|over|resolved|stopped)[.!]?$",
    re.IGNORECASE,
)
_SYMPTOM_PHRASE: Final = re.compile(
    r"^(?:i\s+)?(?:just\s+)?(?:had|have|am having|felt|feel)\s+"
    r"(?:a\s+)?symptom\s+(?:of\s+)?(?P<name>.+?)(?:\s+at\s+(?P<time>\d{1,2}[:.]\d{2}\s*(?:am|pm)?))?[.!]?$",
    re.IGNORECASE,
)
_WEIGHT_ONLY_PHRASE: Final = re.compile(
    r"^(?:please\s+)?(?:add\s+(?:a\s+)?(?:body\s+)?weight\s+of|"
    r"my\s+(?:body\s+)?weight\s+(?:is|was)|i\s+(?:weigh|weighed))\s+"
    r"\d+(?:\.\d+)?\s*(?:lbs?|pounds?|kgs?|kilograms?)"
    r"(?:\s+at\s+\d{1,2}[:.]\d{2}\s*(?:am|pm)?)?[.!]?$",
    re.IGNORECASE,
)
_MEAL_PHRASE: Final = re.compile(
    r"^(?:i\s+)?(?:just\s+)?(?:had|ate|finished)\s+"
    r"(?:(?:a|an|my)\s+)?"
    r"(?:(?P<size>xxl|xl|xs|small|medium|large|extra[\s-]+small|extra[\s-]+large)\s+)?"
    r"(?:meal|breakfast|lunch|dinner)"
    r"(?:\s*(?:,|-|size\s+|with\s+size\s+)?"
    r"(?P<trailing_size>xxl|xl|xs|small|medium|large|extra[\s-]+small|extra[\s-]+large))?"
    r"(?:\s+at\s+(?P<time>\d{1,2}[:.]\d{2}\s*(?:am|pm)?))?[.!]?$",
    re.IGNORECASE,
)
_MEAL_SIZE_ALIASES: Final = {
    "xs": MealSize.XS,
    "extra small": MealSize.XS,
    "extra-small": MealSize.XS,
    "small": MealSize.S,
    "medium": MealSize.M,
    "large": MealSize.L,
    "xl": MealSize.XL,
    "extra large": MealSize.XL,
    "extra-large": MealSize.XL,
    "xxl": MealSize.XXL,
}
_PLANNED_DOSE_PHRASE: Final = re.compile(
    r"^(?:i\s+)?(?:just\s+)?(?:took|have\s+taken|had)\s+(?P<description>.+?)[.!]?$",
    re.IGNORECASE,
)
_PLANNED_DOSE_TERMS: Final = re.compile(
    r"\b(?:regular|scheduled|morning|afternoon|evening|night|dose|doses|meds?|"
    r"medication|medicine|hydrocortisone|fludrocortisone|fludrocortidone|"
    r"flugercortisone|florinef)\b",
    re.IGNORECASE,
)
_PLANNED_DOSE_NEGATION: Final = re.compile(
    r"\b(?:did\s+not|didn't|have\s+not|haven't|not\s+taken|forgot|missed)\b",
    re.IGNORECASE,
)
_PLANNED_DOSE_EXPLICIT_AMOUNT: Final = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mcg|ug|mg|g)\b",
    re.IGNORECASE,
)
_PLANNED_DOSE_NON_SCHEDULED: Final = re.compile(
    r"\b(?:stress(?:[-\s]?dose)?|up[-\s]?dose|emergency)\b",
    re.IGNORECASE,
)
_PLAN_MEDICATION_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "hydrocortisone": ("hydrocortisone",),
    "fludrocortisone": (
        "fludrocortisone",
        "fludrocortidone",
        "flugercortisone",
        "florinef",
    ),
}
_BEADS_LIST_PHRASE: Final = re.compile(
    r"^(?:please\s+)?(?:show|give|send|get|tell)\s+(?:me\s+)?(?:the\s+)?"
    r"(?:current\s+)?(?:bd|beads?)\s+(?:list|issues?|tasks?)[?.!]*$",
    re.IGNORECASE,
)
_BEADS_STATUS_PHRASE: Final = re.compile(
    r"^(?:please\s+)?(?:show|give|send|get|tell)\s+(?:me\s+)?(?:the\s+)?"
    r"(?:current\s+)?(?:bd|beads?)\s+(?:status|summary)[?.!]*$",
    re.IGNORECASE,
)
_BEADS_ADD_PHRASE: Final = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+|please\s+)?"
    r"(?:add|create|file|report)\s+(?:(?:a|an|the)\s+)?(?:new\s+)?"
    r"(?:bd|bead|feature(?:\s+request)?|bug(?:\s+report)?)"
    r"(?:\s+(?:that|to|for)\b|\s*[:.\-])?\s*(?P<request>.+)$",
    re.IGNORECASE,
)

# Public command registry used by the in-app Help drift gate. Commands must be in
# this set to reach dispatch below; adding one therefore requires documenting it.
SUPPORTED_TELEGRAM_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "beads-add",
        "bd-add",
        "bd-list",
        "bd-status",
        "bp",
        "dose",
        "diary",
        "edit",
        "episode",
        "help",
        "injection",
        "lifeevent",
        "location",
        "meal",
        "privacy",
        "start",
        "symptom",
        "today",
        "temperature",
        "undo",
        "weight",
    }
)

HELP_TEXT: Final = """\
HealthCurve bot

Just describe what happened and I'll show you a draft to confirm.
Example: "Took 15mg hydrocortisone at 7:08, slept badly, mild nausea"
For a scheduled dose, you can also say "I took my morning doses" or
"I took my afternoon hydrocortisone." HealthCurve will fill in the exact
medication and amount from the approved plan that applied when you sent it.

Nothing is recorded until you confirm it.

Recording commands (these work even if the language model is offline):
/dose <amount> <medication> [HH:MM] - record a dose
/bp <systolic>/<diastolic> [pulse] [lying|sitting|standing] [HH:MM] - record blood pressure
/weight <value> <lb|lbs|kg|kgs> [HH:MM] - record body weight
/temperature <value> [F|C] [HH:MM] - record body temperature (unit inferred when omitted)
/meal [XS|S|M|L|XL|XXL] [HH:MM] - record a meal (size is optional)
/symptom <name> [0-10] [category=<category>] - record a symptom
/diary <text> [--time=HH:MM] [--sensitive] - record a diary entry
/lifeevent <category> <title> [--time=HH:MM] [--sensitive] - record a life event
/injection <amount> - log an emergency injection
/episode start <trigger> - open a stress episode
/episode end - close the open episode
/today - what's recorded today vs your plan
/location - add optional coarse location to the pending draft
/edit <number> <field> <value> - correct amount, unit, time, or medication
/undo - cancel the pending draft
/privacy - what this bot stores
/help - this message

Project tasks:
/bd-list - return the current bd list through the safe host bridge
/bd-status - return the current bd status through the safe host bridge
/bd-add <feature request> - evaluate, deduplicate, and add a structured inbox Bead
/beads-add <feature request> - compatibility alias for /bd-add
"""

PRIVACY_TEXT: Final = """\
What this bot stores

- Your message text is kept only while a draft is waiting for you. Once you confirm
  or cancel, the raw text is deleted and only the structured record remains.
- Telegram itself keeps your chat history. That is outside HealthCurve's control.
  Clear the chat there if that matters to you.
- /bd-list and /bd-status queue only a fixed operation name for the trusted local host
  bridge. Message text never becomes a command or command argument.
- /bd-add (and the older /beads-add alias) sends only the feature text to the
  configured local model. A successful
  outbox item and Bead retain the generated proposal and a one-way message hash, not
  the raw Telegram directive. Invalid or unavailable model output creates nothing.
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
    chat_id: int | None = None,
) -> Reply:
    """Entry point for an inbound text message."""
    now = now or datetime.now(UTC)
    text = text.strip()
    if not text:
        return Reply("I didn't get any text. Try /help.")

    if text.startswith("/"):
        if chat_id is not None:
            conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)
        return _handle_command(
            session,
            owner,
            text,
            message_id=message_id,
            client=client,
            limiter=limiter,
            model_policy=model_policy,
            now=now,
            chat_id=chat_id,
        )

    if chat_id is not None and _is_conversational_shortcut(text):
        # A complete new request supersedes an unanswered clarification. Clear first
        # so a new Beads request may safely install its own pending question.
        conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)

    conversational = _handle_conversational_shortcut(
        session,
        owner,
        text,
        message_id=message_id,
        client=client,
        limiter=limiter,
        model_policy=model_policy,
        now=now,
        chat_id=chat_id,
    )
    if conversational is not None:
        return conversational

    if chat_id is not None:
        pending = conversation.pending_intent(session, owner_id=owner.id, chat_id=chat_id, now=now)
        if pending is not None:
            return _resume_pending_intent(
                session,
                owner,
                chat_id=chat_id,
                pending=pending,
                answer=text,
                message_id=message_id,
                client=client,
                limiter=limiter,
                model_policy=model_policy,
                now=now,
            )

    return _handle_free_text(
        session,
        owner,
        text,
        message_id=message_id,
        client=client,
        limiter=limiter,
        model_policy=model_policy,
        now=now,
        chat_id=chat_id,
    )


def _is_conversational_shortcut(text: str) -> bool:
    return _looks_like_planned_dose_shorthand(text) or any(
        pattern.fullmatch(text) is not None
        for pattern in (
            _EPISODE_END_PHRASE,
            _EPISODE_START_PHRASE,
            _BEADS_LIST_PHRASE,
            _BEADS_STATUS_PHRASE,
            _BEADS_ADD_PHRASE,
            _WEIGHT_ONLY_PHRASE,
            _MEAL_PHRASE,
            _SYMPTOM_PHRASE,
        )
    )


def _handle_conversational_shortcut(
    session: Session,
    owner: Owner,
    text: str,
    *,
    message_id: str | None,
    client: OllamaClient | None,
    limiter: RateLimiter | None,
    model_policy: RateLimitPolicy | None,
    now: datetime,
    chat_id: int | None,
) -> Reply | None:
    """Handle only narrow, unambiguous phrases; everything else uses extraction."""
    if _EPISODE_END_PHRASE.fullmatch(text):
        return _cmd_episode(session, owner, ["end"], now=now)
    episode = _EPISODE_START_PHRASE.fullmatch(text)
    if episode is not None:
        trigger = (episode.group("trigger") or "unspecified").strip(" .")
        return _cmd_episode(session, owner, ["start", trigger], now=now)
    if _BEADS_LIST_PHRASE.fullmatch(text):
        return _cmd_bd_operation(BeadsOperation.LIST, message_id=message_id, now=now)
    if _BEADS_STATUS_PHRASE.fullmatch(text):
        return _cmd_bd_operation(BeadsOperation.STATUS, message_id=message_id, now=now)
    bead = _BEADS_ADD_PHRASE.fullmatch(text)
    if bead is not None:
        return _cmd_bd_add(
            session,
            owner,
            text,
            message_id=message_id,
            client=client,
            limiter=limiter,
            model_policy=model_policy,
            now=now,
            chat_id=chat_id,
        )

    if looks_like_deterministic_health_entry(text):
        fast_started = monotonic()
        deterministic = extract_deterministically(
            session,
            owner_id=owner.id,
            message=text,
            timezone=owner.default_timezone,
            now=now,
        )
        if deterministic is not None:
            if chat_id is not None:
                conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)
            draft = _store_draft(
                session,
                owner,
                deterministic.candidates,
                raw_text=text,
                source="telegram_fast",
                message_id=message_id,
            )
            log.info(
                "telegram message routed",
                integration="telegram",
                route=deterministic.route,
                outcome="draft",
                latency_ms=int((monotonic() - fast_started) * 1000),
            )
            return _draft_reply(draft, deterministic.candidates)

    planned_dose = _planned_dose_draft(session, owner, text, now=now)
    if planned_dose is not None:
        return planned_dose

    explicit_weight = find_explicit_weight(text) if _WEIGHT_ONLY_PHRASE.fullmatch(text) else None
    if explicit_weight is not None:
        raw_value, unit = explicit_weight
        local = _local_now(owner, now)
        stated_time = find_time_expression(text)
        flags: list[FlagCode] = []
        if stated_time is not None:
            parsed = _parse_time_token(stated_time, local)
            if parsed is None:
                return None
            local = parsed
        else:
            flags.append(FlagCode.ASSUMED_TIME)
        candidate = ValidatedCandidate(
            type=CandidateType.WEIGHT,
            weight_value=Decimal(raw_value),
            weight_unit=unit,
            measurement_setting=explicit_measurement_setting(text),
            local_time=local,
            timezone=owner.default_timezone,
            confidence=1.0,
            flags=flags,
        )
        draft = _store_draft(
            session, owner, [candidate], raw_text=text, source="telegram_conversational"
        )
        return _draft_reply(draft, [candidate])

    meal = _MEAL_PHRASE.fullmatch(text)
    if meal is not None:
        raw_size = meal.group("size") or meal.group("trailing_size")
        size = _meal_size(raw_size) if raw_size else None
        local = _local_now(owner, now)
        flags = []
        if meal.group("time"):
            parsed = _parse_time_token(meal.group("time"), local)
            if parsed is None:
                return None
            local = parsed
        else:
            flags.append(FlagCode.ASSUMED_TIME)
        candidate = ValidatedCandidate(
            type=CandidateType.MEAL,
            meal_size=size,
            local_time=local,
            timezone=owner.default_timezone,
            confidence=1.0,
            flags=flags,
        )
        draft = _store_draft(
            session, owner, [candidate], raw_text=text, source="telegram_conversational"
        )
        return _draft_reply(draft, [candidate])

    symptom = _SYMPTOM_PHRASE.fullmatch(text)
    if symptom is not None:
        name = symptom.group("name").strip(" .")
        local = _local_now(owner, now)
        flags = []
        if symptom.group("time"):
            parsed = _parse_time_token(symptom.group("time"), local)
            if parsed is None:
                return None
            local = parsed
        else:
            flags.append(FlagCode.ASSUMED_TIME)
        candidate = ValidatedCandidate(
            type=CandidateType.SYMPTOM,
            symptom_name=name,
            local_time=local,
            timezone=owner.default_timezone,
            confidence=1.0,
            flags=flags,
        )
        draft = _store_draft(
            session, owner, [candidate], raw_text=text, source="telegram_conversational"
        )
        return _draft_reply(draft, [candidate])
    return None


def _planned_dose_draft(
    session: Session, owner: Owner, text: str, *, now: datetime
) -> Reply | None:
    """Resolve narrow completed-dose shorthand from the approved historical plan.

    The Telegram provider timestamp is passed as ``now`` by dispatch. That makes a
    delayed message resolve against the plan that was effective when the owner sent
    it, not whichever plan happens to be active when a worker later processes it.
    Amounts and routes come only from an approved plan and remain confirmation drafts.
    """
    match = _PLANNED_DOSE_PHRASE.fullmatch(text)
    if match is None or not _looks_like_planned_dose_shorthand(text):
        return None
    description = match.group("description").strip()

    local = _local_now(owner, now)
    flags: list[FlagCode] = []
    stated_time = find_time_expression(text)
    if stated_time is None:
        flags.append(FlagCode.ASSUMED_TIME)
    else:
        parsed = normalise_local_time(stated_time, local)
        if parsed is None:  # defensive: finder and normalizer intentionally share a grammar
            return Reply(
                "I couldn't read the time in that scheduled-dose message. Nothing was "
                "recorded; include the time as HH:MM."
            )
        local = parsed
        if is_nonexistent(local, owner.default_timezone):
            flags.append(FlagCode.NONEXISTENT_TIME)
        elif is_ambiguous(local, owner.default_timezone):
            flags.append(FlagCode.AMBIGUOUS_TIME)

    period = _planned_dose_period(description, local.time())
    if period is None:
        return Reply(
            "I can use your approved plan, but I can't tell whether you mean the "
            "morning, afternoon, or evening dose. Nothing was recorded; tell me which one."
        )

    version = meds.active_version_at(session, owner.id, now)
    if version is None:
        return Reply(
            f"I couldn't find an approved medication plan in effect when this message was "
            f"sent ({local:%Y-%m-%d %H:%M}). Nothing was recorded; include the medication "
            "and amount explicitly."
        )

    slots = [slot for slot in version.slots if _slot_period(slot) == period]
    named_medications = _named_plan_medications(description, slots)
    if named_medications is not None:
        slots = [slot for slot in slots if slot.medication_id in named_medications]
    if not slots:
        subject = "the named medication" if named_medications is not None else f"a {period} dose"
        return Reply(
            f"Your approved plan in effect when this was sent does not contain {subject}. "
            "Nothing was recorded; include the medication and amount explicitly if needed."
        )

    conditional = [slot for slot in slots if slot.condition and slot.condition.strip()]
    if conditional:
        names = ", ".join(sorted({slot.medication.name for slot in conditional}))
        return Reply(
            f"The matching approved-plan slot for {names} has a condition, so I won't assume "
            "it was due. Nothing was recorded; include the medication and amount explicitly."
        )

    by_medication: dict[uuid.UUID, list[RegimenDoseSlot]] = {}
    for slot in slots:
        by_medication.setdefault(slot.medication_id, []).append(slot)
    ambiguous = [items for items in by_medication.values() if len(items) > 1]
    if ambiguous:
        names = ", ".join(sorted({items[0].medication.name for items in ambiguous}))
        return Reply(
            f"Your approved plan has more than one {period} slot for {names}, so I can't "
            "tell which one you took. Nothing was recorded; include the time or amount."
        )

    candidates = [
        ValidatedCandidate(
            type=CandidateType.DOSE,
            medication_id=slot.medication_id,
            medication_name=slot.medication.name,
            amount=slot.amount,
            unit=slot.unit.value,
            route=slot.route.value,
            dose_category=DoseCategory.SCHEDULED,
            local_time=local,
            timezone=owner.default_timezone,
            confidence=1.0,
            flags=flags,
            is_actionable=not bool(BLOCKING_FLAGS & set(flags)),
        )
        for slot in sorted(slots, key=lambda item: (item.sort_order, item.medication.name, item.id))
    ]
    draft = _store_draft(
        session,
        owner,
        candidates,
        raw_text=text,
        source="telegram_plan_shorthand",
    )
    return _draft_reply(draft, candidates)


def _planned_dose_period(description: str, sent_local_time: time) -> str | None:
    lowered = description.lower()
    explicit = [
        period
        for period, terms in (
            ("morning", ("morning",)),
            ("afternoon", ("afternoon",)),
            ("evening", ("evening", "night")),
        )
        if any(re.search(rf"\b{term}\b", lowered) for term in terms)
    ]
    if len(explicit) == 1:
        return explicit[0]
    if explicit:
        return None
    if sent_local_time < time(9, 30):
        return "morning"
    if time(12) <= sent_local_time < time(18):
        return "afternoon"
    if sent_local_time >= time(18):
        return "evening"
    return None


def _looks_like_planned_dose_shorthand(text: str) -> bool:
    match = _PLANNED_DOSE_PHRASE.fullmatch(text)
    if match is None:
        return False
    description = match.group("description")
    return (
        _PLANNED_DOSE_NEGATION.search(description) is None
        and _PLANNED_DOSE_EXPLICIT_AMOUNT.search(description) is None
        and _PLANNED_DOSE_NON_SCHEDULED.search(description) is None
        and _PLANNED_DOSE_TERMS.search(description) is not None
    )


def _slot_period(slot: RegimenDoseSlot) -> str:
    if slot.timing_mode is DoseTimingMode.WAKE:
        return "morning"
    scheduled = slot.scheduled_local_time
    assert scheduled is not None
    if scheduled < time(12):
        return "morning"
    if scheduled < time(18):
        return "afternoon"
    return "evening"


def _named_plan_medications(
    description: str, slots: list[RegimenDoseSlot]
) -> set[uuid.UUID] | None:
    lowered = description.lower()
    requested_families = {
        family
        for family, aliases in _PLAN_MEDICATION_ALIASES.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases)
    }
    if not requested_families:
        return None
    return {
        slot.medication_id
        for slot in slots
        if any(slot.medication.normalized_name.startswith(family) for family in requested_families)
    }


# ---------------------------------------------------------------------------
# Commands (/bd-add uses the model only to create bounded backlog data)
# ---------------------------------------------------------------------------


def _handle_command(
    session: Session,
    owner: Owner,
    text: str,
    *,
    message_id: str | None,
    client: OllamaClient | None,
    limiter: RateLimiter | None,
    model_policy: RateLimitPolicy | None,
    now: datetime,
    chat_id: int | None,
) -> Reply:
    command_part, *remainder = text.split(maxsplit=1)
    command = command_part.lower().lstrip("/").split("@")[0]
    raw_argument = remainder[0] if remainder else ""
    args = raw_argument.split()

    if command not in SUPPORTED_TELEGRAM_COMMANDS:
        return Reply(f"Unknown command /{command}. Try /help.")

    match command:
        case "help" | "start":
            return Reply(HELP_TEXT)
        case "privacy":
            return Reply(PRIVACY_TEXT)
        case "dose":
            return _cmd_dose(session, owner, args, now=now)
        case "bp":
            return _cmd_blood_pressure(session, owner, args, now=now)
        case "weight":
            return _cmd_weight(session, owner, args, now=now)
        case "temperature":
            return _cmd_temperature(session, owner, args, now=now)
        case "meal":
            return _cmd_meal(session, owner, args, now=now)
        case "diary":
            return _cmd_diary(session, owner, args, now=now)
        case "lifeevent":
            return _cmd_life_event(session, owner, args, now=now)
        case "bd-list":
            if raw_argument:
                return Reply("Usage: /bd-list (no arguments)")
            return _cmd_bd_operation(BeadsOperation.LIST, message_id=message_id, now=now)
        case "bd-status":
            if raw_argument:
                return Reply("Usage: /bd-status (no arguments)")
            return _cmd_bd_operation(BeadsOperation.STATUS, message_id=message_id, now=now)
        case "bd-add" | "beads-add":
            return _cmd_bd_add(
                session,
                owner,
                raw_argument,
                message_id=message_id,
                client=client,
                limiter=limiter,
                model_policy=model_policy,
                now=now,
                chat_id=chat_id,
            )
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
            return _cmd_undo(session, owner, chat_id=chat_id)
        case _:  # pragma: no cover - registry and dispatch are checked together
            raise AssertionError(f"registered Telegram command is not dispatched: {command}")


def _cmd_bd_operation(
    operation: BeadsOperation,
    *,
    message_id: str | None,
    now: datetime,
) -> Reply:
    """Queue one fixed read-only Beads operation for the trusted host bridge."""
    operation_name = {
        BeadsOperation.LIST: "current Beads issue list",
        BeadsOperation.STATUS: "current Beads project status",
    }[operation]
    settings = get_settings()
    if settings.beads_outbox_dir is None:
        return Reply("Beads status is temporarily unavailable. Nothing was run; try again later.")
    safe_message_id = message_id or ""
    try:
        existing = queued_operation(settings.beads_outbox_dir, message_id=safe_message_id)
        if existing is not None:
            return Reply(
                f"I'm already getting the {operation_name}. "
                "I'll post it here as soon as it's ready."
            )
        queue_operation(
            settings.beads_outbox_dir,
            message_id=safe_message_id,
            operation=operation,
            now=now,
        )
    except FeatureRequestRejected:
        return Reply("Beads status is temporarily unavailable. Nothing was run; try again later.")
    acknowledgement = f"Got it — I'm getting the {operation_name} now."
    return Reply(f"{acknowledgement} I'll post it here as soon as it's ready.")


def _remember_beads_clarification(
    session: Session,
    owner: Owner,
    chat_id: int | None,
    request: str,
    question: str,
    *,
    now: datetime,
) -> Reply:
    reply = Reply(f"I need one clarification before creating a Bead:\n{question}")
    if chat_id is not None:
        conversation.remember_exchange(
            session,
            owner_id=owner.id,
            chat_id=chat_id,
            user_text=request,
            assistant_text=reply.text,
            pending=conversation.PendingIntent(
                kind="beads_add", request=request, question=question
            ),
            now=now,
        )
    return reply


def _resume_pending_intent(
    session: Session,
    owner: Owner,
    *,
    chat_id: int,
    pending: conversation.PendingIntent,
    answer: str,
    message_id: str | None,
    client: OllamaClient | None,
    limiter: RateLimiter | None,
    model_policy: RateLimitPolicy | None,
    now: datetime,
) -> Reply:
    if answer.casefold().strip(" .!") in {"cancel", "never mind", "nevermind", "stop"}:
        conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)
        return Reply("Cancelled. I didn't create a Bead.")
    if pending.kind == "beads_add":
        return _cmd_bd_add(
            session,
            owner,
            pending.request,
            message_id=message_id,
            client=client,
            limiter=limiter,
            model_policy=model_policy,
            now=now,
            chat_id=chat_id,
            clarification_answer=answer,
        )
    conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)
    return Reply("That earlier question expired. Please send the request again.")


def _cmd_bd_add(
    session: Session,
    owner: Owner,
    request: str,
    *,
    message_id: str | None,
    client: OllamaClient | None,
    limiter: RateLimiter | None,
    model_policy: RateLimitPolicy | None,
    now: datetime,
    chat_id: int | None,
    clarification_answer: str | None = None,
) -> Reply:
    """Evaluate locally and queue only a validated proposal, never raw text."""
    settings = get_settings()
    if settings.beads_outbox_dir is None:
        return Reply(
            "Feature-request capture is temporarily unavailable. "
            "Nothing was created; try again later."
        )
    safe_message_id = message_id or ""
    try:
        validate_request(request, allow_high_risk_clarification=clarification_answer is not None)
        if clarification_answer is not None:
            validate_clarification_answer(clarification_answer)
        existing = queued_request(settings.beads_outbox_dir, message_id=safe_message_id)
    except FeatureRequestNeedsClarification as exc:
        return _remember_beads_clarification(
            session, owner, chat_id, request, exc.question, now=now
        )
    except FeatureRequestRejected as exc:
        if str(exc) == "request_too_long":
            return Reply("That feature request is too long. Keep it to 500 characters or fewer.")
        if str(exc) == "request_may_contain_private_data":
            return Reply(
                "Please describe only the feature—remove passwords, tokens, contact details, "
                "and personal health values."
            )
        if str(exc) == "request_contains_model_instructions":
            return Reply(
                "I couldn't safely evaluate that as a product request. Describe only the "
                "feature outcome, without instructions to the language model."
            )
        return Reply(
            "Usage: /bd-add <feature request>\n"
            "Example: /bd-add add a feature that allows me to record hydration\n"
            "The older /beads-add spelling remains a compatibility alias."
        )
    if existing is not None:
        return Reply(
            "That feature request is already on its way to the HealthCurve task list. "
            "I'll post the Bead ID here as soon as it's ready."
        )
    if limiter is not None and model_policy is not None:
        try:
            limiter.check("model", "telegram-feature-request", model_policy)
        except RateLimitExceeded as exc:
            return Reply(
                "The local feature-request evaluator is rate limited. Nothing was created. "
                f"Try again in about {exc.result.retry_after} seconds."
            )
        except RateLimitUnavailable:
            return Reply(
                "I can't safely check the feature-request limit right now. Nothing was created."
            )
    try:
        evaluated = evaluate_request(
            request, client=client, clarification_answer=clarification_answer
        )
        queue_request(
            settings.beads_outbox_dir,
            message_id=safe_message_id,
            evaluated=evaluated,
            backlog_epic_id=settings.beads_backlog_epic_id,
            now=now,
        )
    except FeatureRequestNeedsClarification as exc:
        return _remember_beads_clarification(
            session, owner, chat_id, request, exc.question, now=now
        )
    except FeatureRequestEvaluationFailed:
        return Reply(
            "The local language model couldn't safely evaluate that feature request. "
            "Nothing was created; try again later or rephrase it more specifically."
        )
    except FeatureRequestRejected:
        return Reply("That request could not be queued safely. Nothing was created.")
    if chat_id is not None:
        conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)
    return Reply(
        f'Got it — I\'m adding "{evaluated.proposal.title}" to the HealthCurve task list. '
        "I'll post the Bead ID here as soon as it's ready. "
        "This creates a task only; it doesn't start any work."
    )


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
        dose_category=DoseCategory.SCHEDULED,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_symptom(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    if not args:
        return Reply(
            "Usage: /symptom <name> [severity 0-10] [category=<category>]\n"
            "Categories: glucocorticoid, mineralocorticoid, postural, other.\n"
            "Example: /symptom dizziness 4 category=postural"
        )

    category: SymptomTrackingCategory | None = None
    category_args = [token for token in args if token.lower().startswith("category=")]
    if len(category_args) > 1:
        return Reply("Include at most one category=<category> value.")
    if category_args:
        category_token = category_args[0]
        try:
            category = SymptomTrackingCategory(category_token.split("=", maxsplit=1)[1].lower())
        except ValueError:
            return Reply("Category must be glucocorticoid, mineralocorticoid, postural, or other.")
        args = [token for token in args if not token.lower().startswith("category=")]

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
        symptom_tracking_category=category,
        local_time=_local_now(owner, now),
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_meal(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = "Usage: /meal [XS|S|M|L|XL|XXL] [HH:MM]\nExample: /meal L 12:30"
    if len(args) > 2:
        return Reply(usage)
    local = _local_now(owner, now)
    size: MealSize | None = None
    has_explicit_time = False
    for token in args:
        parsed_size = _meal_size(token)
        if parsed_size is not None:
            if size is not None:
                return Reply(usage)
            size = parsed_size
            continue
        parsed_time = _parse_time_token(token, local)
        if parsed_time is None or (parsed_time == local and not _looks_like_time(token)):
            return Reply(usage)
        local = parsed_time
        has_explicit_time = True
    candidate = ValidatedCandidate(
        type=CandidateType.MEAL,
        meal_size=size,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[] if has_explicit_time else [FlagCode.ASSUMED_TIME],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _context_command_parts(
    args: list[str], local_reference: datetime
) -> tuple[list[str], datetime, bool, bool] | None:
    content: list[str] = []
    local = local_reference
    sensitive = False
    has_explicit_time = False
    for argument in args:
        if argument == "--sensitive":
            if sensitive:
                return None
            sensitive = True
            continue
        if argument.startswith("--time="):
            if has_explicit_time:
                return None
            parsed = _parse_time_token(argument.removeprefix("--time="), local_reference)
            if parsed is None:
                return None
            local = parsed
            has_explicit_time = True
            continue
        if argument.startswith("--"):
            return None
        content.append(argument)
    return content, local, sensitive, has_explicit_time


def _cmd_diary(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = (
        "Usage: /diary <text> [--time=HH:MM] [--sensitive]\n"
        "Example: /diary Slept poorly --time=07:30 --sensitive"
    )
    parsed = _context_command_parts(args, _local_now(owner, now))
    if parsed is None:
        return Reply(usage)
    content, local, sensitive, has_explicit_time = parsed
    entry = " ".join(content).strip()
    if not entry:
        return Reply(usage)
    candidate = ValidatedCandidate(
        type=CandidateType.DIARY,
        text=entry,
        is_sensitive=sensitive,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[] if has_explicit_time else [FlagCode.ASSUMED_TIME],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_life_event(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = (
        "Usage: /lifeevent <category> <title> [--time=HH:MM] [--sensitive]\n"
        "Categories: travel, illness, work, exercise, sleep_disruption, stress, "
        "medical_appointment, other\n"
        "Example: /lifeevent travel Overnight flight --time=22:15"
    )
    parsed = _context_command_parts(args, _local_now(owner, now))
    if parsed is None:
        return Reply(usage)
    content, local, sensitive, has_explicit_time = parsed
    if len(content) < 2:
        return Reply(usage)
    try:
        category = LifeEventCategory(content[0].lower().replace("-", "_"))
    except ValueError:
        return Reply(usage)
    title = " ".join(content[1:]).strip()
    if not title:
        return Reply(usage)
    candidate = ValidatedCandidate(
        type=CandidateType.LIFE_EVENT,
        text=title,
        is_sensitive=sensitive,
        life_event_category=category,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[] if has_explicit_time else [FlagCode.ASSUMED_TIME],
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _meal_size(value: str) -> MealSize | None:
    normalized = value.strip().lower().replace("_", " ")
    try:
        return MealSize(normalized)
    except ValueError:
        return _MEAL_SIZE_ALIASES.get(normalized)


def _cmd_blood_pressure(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = (
        "Usage: /bp <systolic>/<diastolic> [pulse] [lying|sitting|standing] [HH:MM]\n"
        "Example: /bp 120/80 62 standing 08:15"
    )
    if not args:
        return Reply(usage)
    values = args[0].split("/", maxsplit=1)
    remaining = args[1:]
    if len(values) != 2 and len(args) >= 2:
        values, remaining = args[:2], args[2:]
    if len(values) != 2 or not all(value.isdigit() for value in values):
        return Reply(usage)
    systolic, diastolic = (int(value) for value in values)
    if not 1 <= systolic <= 500 or not 1 <= diastolic <= 500:
        return Reply("Blood-pressure values must be positive whole numbers at most 500 mmHg.")

    position_tokens = {"lying", "supine", "sitting", "seated", "standing"}
    position_values = {
        BodyPosition.LYING
        if token.lower() in {"lying", "supine"}
        else BodyPosition.SITTING
        if token.lower() in {"sitting", "seated"}
        else BodyPosition.STANDING
        for token in remaining
        if token.lower() in position_tokens
    }
    if len(position_values) > 1:
        return Reply("Include at most one body position: lying, sitting, or standing.")
    position_text = " ".join(remaining)
    body_position = explicit_body_position(position_text)
    remaining = [token for token in remaining if token.lower() not in position_tokens]
    time_token = remaining[-1] if remaining and _looks_like_time(remaining[-1]) else None
    pulse_parts = remaining[:-1] if time_token else remaining
    if len(pulse_parts) > 1 or (pulse_parts and not pulse_parts[0].isdigit()):
        return Reply(usage)
    pulse = int(pulse_parts[0]) if pulse_parts else None
    if pulse is not None and not 1 <= pulse <= 500:
        return Reply("Pulse must be a positive whole number at most 500 bpm.")

    local = _local_now(owner, now)
    if time_token:
        parsed = _parse_time_token(time_token, local)
        if parsed is None:
            return Reply(f"I couldn't read '{time_token}' as a time. Use HH:MM.")
        local = parsed
    candidate = ValidatedCandidate(
        type=CandidateType.BLOOD_PRESSURE,
        systolic_mmhg=systolic,
        diastolic_mmhg=diastolic,
        pulse_bpm=pulse,
        measurement_setting=MeasurementSetting.HOME,
        body_position=body_position,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_weight(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = "Usage: /weight <value> <lb|lbs|kg|kgs> [HH:MM]\nExample: /weight 180 lbs 08:15"
    if len(args) < 2:
        return Reply(usage)
    try:
        value = Decimal(args[0])
        unit_alias = {
            "lb": WeightUnit.LB,
            "lbs": WeightUnit.LB,
            "kg": WeightUnit.KG,
            "kgs": WeightUnit.KG,
        }
        unit = unit_alias[args[1].lower()]
    except (InvalidOperation, KeyError, ValueError):
        return Reply(usage)
    if not Decimal(0) < value <= Decimal(5000):
        return Reply("Weight must be greater than zero and at most 5000 lb or kg.")
    if len(args) > 3:
        return Reply(usage)
    time_token = args[2] if len(args) == 3 else None
    local = _local_now(owner, now)
    if time_token:
        parsed = _parse_time_token(time_token, local)
        if parsed is None:
            return Reply(f"I couldn't read '{time_token}' as a time. Use HH:MM.")
        local = parsed
    candidate = ValidatedCandidate(
        type=CandidateType.WEIGHT,
        weight_value=value,
        weight_unit=unit,
        measurement_setting=MeasurementSetting.HOME,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
    )
    draft = _store_draft(session, owner, [candidate], raw_text=None, source="telegram_command")
    return _draft_reply(draft, [candidate])


def _cmd_temperature(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = "Usage: /temperature <value> [F|C] [HH:MM]\nExample: /temperature 98.6 08:15"
    if not args or len(args) > 3:
        return Reply(usage)
    try:
        value = Decimal(args[0])
    except InvalidOperation:
        return Reply(usage)

    remaining = args[1:]
    unit: TemperatureUnit | None = None
    inferred_unit = False
    if remaining:
        try:
            unit = TemperatureUnit(remaining[0].lower().replace("°", ""))
        except ValueError:
            pass
        else:
            remaining = remaining[1:]
    if unit is None:
        unit = vitals.infer_temperature_unit(value)
        inferred_unit = unit is not None

    if unit is None:
        return Reply("Temperature must be between 25 and 45 °C (77 and 113 °F).")
    if not vitals.temperature_in_range(value, unit):
        return Reply("Temperature must be between 25 and 45 °C (77 and 113 °F).")
    if len(remaining) > 1:
        return Reply(usage)
    time_token = remaining[0] if remaining else None
    local = _local_now(owner, now)
    if time_token:
        parsed = _parse_time_token(time_token, local)
        if parsed is None:
            return Reply(f"I couldn't read '{time_token}' as a time. Use HH:MM.")
        local = parsed
    candidate = ValidatedCandidate(
        type=CandidateType.TEMPERATURE,
        temperature_value=value,
        temperature_unit=unit,
        local_time=local,
        timezone=owner.default_timezone,
        confidence=1.0,
        flags=[FlagCode.INFERRED_TEMPERATURE_UNIT] if inferred_unit else [],
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

    lines = [
        f"Today ({local_today.isoformat()}, {timezone_abbreviation(owner.default_timezone, now)})",
        "",
    ]
    slots = cast(list[meds.SlotComparison], comparison["slots"])
    if not slots:
        lines.append("Nothing recorded, and no approved plan for today.")
    for slot in sorted(slots, key=lambda item: _today_slot_sort_key(item, local_today)):
        if slot.status == "missing":
            if slot.timing_mode is DoseTimingMode.WAKE:
                plan_time = "when waking"
            elif isinstance(slot.scheduled_local_time, time):
                plan_time = f"{slot.scheduled_local_time:%H:%M}"
            else:
                raise AssertionError("missing plan slot has no timing anchor")
            reminder = (
                f"; remind by {slot.reminder_local_time:%H:%M}"
                if slot.timing_mode is DoseTimingMode.WAKE
                and isinstance(slot.reminder_local_time, time)
                else ""
            )
            lines.append(
                f"  [ ] {plan_time}  {slot.medication_name} "
                f"{slot.planned_amount} - not recorded{reminder}"
            )
        elif slot.status == "unplanned":
            lines.append(
                f"  [+] {slot.actual_local_time:%H:%M}  {slot.medication_name} "
                f"{slot.actual_amount} - extra"
            )
        else:
            mark = "x" if slot.status in {"on_time", "recorded"} else "~"
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


def _today_slot_sort_key(slot: meds.SlotComparison, day: date) -> tuple[datetime, int, str, str]:
    """Order /today rows by the local time printed for that row.

    Recorded rows use experienced time; absent plan slots use scheduled time. At
    equal times, recorded facts precede derived absences, then medication and row ID
    keep the output stable without changing matching or totals.
    """
    if slot.actual_local_time is not None:
        displayed_at = slot.actual_local_time.replace(tzinfo=None)
        row_kind = 0
        row_id = slot.dose_id
    else:
        display_time = (
            slot.reminder_local_time
            if slot.timing_mode is DoseTimingMode.WAKE
            else slot.scheduled_local_time
        )
        if not isinstance(display_time, time):
            raise AssertionError("today comparison row has no display time")
        displayed_at = datetime.combine(day, display_time)
        row_kind = 1
        row_id = slot.slot_id
    return displayed_at, row_kind, slot.medication_name.casefold(), str(row_id or "")


def _cmd_undo(session: Session, owner: Owner, *, chat_id: int | None = None) -> Reply:
    if chat_id is not None:
        conversation.clear_context(session, owner_id=owner.id, chat_id=chat_id)
    draft = _pending_draft(session, owner.id)
    if draft is None:
        return Reply("Nothing pending to undo.")
    draft.state = DraftState.CANCELLED
    draft.resolved_at = datetime.now(UTC)
    draft.purge_raw_text()
    location.cancel_for_draft(session, owner.id, draft.id)
    return Reply("Cancelled. Nothing was recorded.")


def _cmd_edit(session: Session, owner: Owner, args: list[str], *, now: datetime) -> Reply:
    usage = "Usage: /edit <number> <amount|unit|time|medication|category> <value>"
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
    if candidate.type in {
        CandidateType.BLOOD_PRESSURE,
        CandidateType.WEIGHT,
        CandidateType.TEMPERATURE,
    }:
        return _edit_vital_candidate(
            draft,
            candidates,
            index,
            candidate,
            args[1].lower(),
            " ".join(args[2:]).strip(),
            owner,
            now,
        )
    if candidate.type is CandidateType.SYMPTOM:
        return _edit_symptom_candidate(
            draft,
            candidates,
            index,
            candidate,
            args[1].lower(),
            " ".join(args[2:]).strip(),
        )
    if candidate.type is CandidateType.MEAL:
        return _edit_meal_candidate(
            draft,
            candidates,
            index,
            candidate,
            args[1].lower(),
            " ".join(args[2:]).strip(),
            owner,
            now,
        )
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
    elif field == "category":
        normalized = value.lower().replace("_", " ").replace("-", " ").strip()
        if normalized in {"regular", "scheduled"}:
            changes["dose_category"] = DoseCategory.SCHEDULED
        elif normalized in {"stress", "stress dose", "up dose", "updose"}:
            changes["dose_category"] = DoseCategory.STRESS
        else:
            return Reply("Category must be regular or stress dose.")
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


def _edit_vital_candidate(
    draft: ExtractionDraft,
    candidates: list[ValidatedCandidate],
    index: int,
    candidate: ValidatedCandidate,
    field: str,
    value: str,
    owner: Owner,
    now: datetime,
) -> Reply:
    changes: dict[str, object] = {}
    if field == "time":
        local = _parse_time_token(value, _local_now(owner, now))
        if local is None:
            return Reply("I couldn't read that time. Use 24-hour HH:MM, e.g. /edit 1 time 08:15")
        try:
            resolved = resolve_event_time(local, owner.default_timezone)
        except AmbiguousLocalTimeError:
            return Reply("That time happened twice when the clocks changed; use the web editor.")
        except NonExistentLocalTimeError:
            return Reply("That time did not exist when the clocks changed; choose another time.")
        if resolved.occurred_at > now + timedelta(minutes=10):
            return Reply("That time resolves into the future; choose the time the event happened.")
        changes["local_time"] = local
    elif candidate.type is CandidateType.BLOOD_PRESSURE:
        if field not in {"systolic", "diastolic", "pulse", "position", "posture"}:
            return Reply("For blood pressure, edit systolic, diastolic, pulse, position, or time.")
        if field in {"position", "posture"}:
            normalized = value.lower().strip()
            aliases = {
                "lying": BodyPosition.LYING,
                "supine": BodyPosition.LYING,
                "sitting": BodyPosition.SITTING,
                "seated": BodyPosition.SITTING,
                "standing": BodyPosition.STANDING,
            }
            if normalized in {"none", "clear", "unknown"}:
                changes["body_position"] = None
            elif normalized in aliases:
                changes["body_position"] = aliases[normalized]
            else:
                return Reply("Position must be lying, sitting, standing, or none.")
        elif field == "pulse" and value.lower() in {"none", "clear"}:
            changes["pulse_bpm"] = None
        elif not value.isdigit() or not 1 <= int(value) <= 500:
            return Reply("Blood-pressure and pulse values must be whole numbers from 1 to 500.")
        else:
            changes[
                {"systolic": "systolic_mmhg", "diastolic": "diastolic_mmhg", "pulse": "pulse_bpm"}[
                    field
                ]
            ] = int(value)
    elif candidate.type is CandidateType.WEIGHT:
        if field in {"amount", "value"}:
            try:
                amount = Decimal(value)
            except InvalidOperation:
                return Reply("I couldn't read that weight. Example: /edit 1 amount 180")
            if not Decimal(0) < amount <= Decimal(5000):
                return Reply("Weight must be greater than zero and at most 5000.")
            changes["weight_value"] = amount
        elif field == "unit":
            try:
                changes["weight_unit"] = WeightUnit(value.lower())
            except ValueError:
                return Reply("Weight unit must be lb or kg.")
        else:
            return Reply("For weight, edit amount, unit, or time.")
    else:
        if field in {"amount", "value"}:
            try:
                amount = Decimal(value)
            except InvalidOperation:
                return Reply("I couldn't read that temperature. Example: /edit 1 value 98.6")
            unit = candidate.temperature_unit
            if FlagCode.INFERRED_TEMPERATURE_UNIT in candidate.flags:
                unit = vitals.infer_temperature_unit(amount)
                if unit is not None:
                    changes["temperature_unit"] = unit
            if unit is None or not vitals.temperature_in_range(amount, unit):
                return Reply("Temperature must be between 25 and 45 °C (77 and 113 °F).")
            changes["temperature_value"] = amount
        elif field == "unit":
            try:
                unit = TemperatureUnit(value.lower().replace("°", ""))
            except ValueError:
                return Reply("Temperature unit must be F or C.")
            amount = candidate.temperature_value
            if amount is None or not vitals.temperature_in_range(amount, unit):
                return Reply("Temperature must be between 25 and 45 °C (77 and 113 °F).")
            changes["temperature_unit"] = unit
        else:
            return Reply("For temperature, edit value, unit, or time.")

    edited = ValidatedCandidate.model_validate({**candidate.model_dump(mode="python"), **changes})
    flags = list(edited.flags)
    if edited.type is CandidateType.TEMPERATURE and field == "unit":
        _remove_flags(flags, FlagCode.INFERRED_TEMPERATURE_UNIT)
    _remove_flags(
        flags,
        FlagCode.MISSING_VITAL_VALUE,
        FlagCode.INVALID_VITAL_VALUE,
        FlagCode.MISSING_TIME,
        FlagCode.ASSUMED_TIME,
        FlagCode.UNPARSEABLE_TIME,
        FlagCode.AMBIGUOUS_TIME,
        FlagCode.NONEXISTENT_TIME,
        FlagCode.FUTURE_TIME,
    )
    if edited.type is CandidateType.BLOOD_PRESSURE:
        systolic = edited.systolic_mmhg
        diastolic = edited.diastolic_mmhg
        if systolic is None or diastolic is None:
            flags.append(FlagCode.MISSING_VITAL_VALUE)
        elif (
            not 1 <= systolic <= 500
            or not 1 <= diastolic <= 500
            or (edited.pulse_bpm is not None and not 1 <= edited.pulse_bpm <= 500)
        ):
            flags.append(FlagCode.INVALID_VITAL_VALUE)
    if edited.type is CandidateType.WEIGHT:
        if edited.weight_value is None or edited.weight_unit is None:
            flags.append(FlagCode.MISSING_VITAL_VALUE)
        elif not Decimal(0) < edited.weight_value <= Decimal(5000):
            flags.append(FlagCode.INVALID_VITAL_VALUE)
    if edited.type is CandidateType.TEMPERATURE:
        if edited.temperature_value is None or edited.temperature_unit is None:
            flags.append(FlagCode.MISSING_VITAL_VALUE)
        elif not vitals.temperature_in_range(edited.temperature_value, edited.temperature_unit):
            flags.append(FlagCode.INVALID_VITAL_VALUE)
    edited = edited.model_copy(
        update={
            "flags": flags,
            "is_actionable": not bool(BLOCKING_FLAGS & set(flags)),
        }
    )
    if draft.original_candidates is None:
        draft.original_candidates = [dict(item) for item in draft.candidates]
    candidates[index] = edited
    draft.candidates = [item.model_dump(mode="json") for item in candidates]
    draft.state = DraftState.EDITED
    return _draft_reply(draft, candidates, edited=True)


def _edit_symptom_candidate(
    draft: ExtractionDraft,
    candidates: list[ValidatedCandidate],
    index: int,
    candidate: ValidatedCandidate,
    field: str,
    value: str,
) -> Reply:
    if field not in {"category", "tracking-category"}:
        return Reply("For a symptom, edit category only.")
    normalized = value.lower().replace("_", "-").strip()
    if normalized in {"none", "clear", "unknown", "not-recorded"}:
        category = None
    else:
        try:
            category = SymptomTrackingCategory(normalized)
        except ValueError:
            return Reply(
                "Category must be glucocorticoid, mineralocorticoid, postural, other, or none."
            )
    edited = candidate.model_copy(update={"symptom_tracking_category": category})
    if draft.original_candidates is None:
        draft.original_candidates = [dict(item) for item in draft.candidates]
    candidates[index] = edited
    draft.candidates = [item.model_dump(mode="json") for item in candidates]
    draft.state = DraftState.EDITED
    return _draft_reply(draft, candidates, edited=True)


def _edit_meal_candidate(
    draft: ExtractionDraft,
    candidates: list[ValidatedCandidate],
    index: int,
    candidate: ValidatedCandidate,
    field: str,
    value: str,
    owner: Owner,
    now: datetime,
) -> Reply:
    changes: dict[str, object] = {}
    if field == "size":
        if value.lower() in {"none", "unknown", "clear"}:
            changes["meal_size"] = None
        else:
            size = _meal_size(value)
            if size is None:
                return Reply("Meal size must be XS, S, M, L, XL, XXL, or none.")
            changes["meal_size"] = size
    elif field == "time":
        local = _parse_time_token(value, _local_now(owner, now))
        if local is None:
            return Reply("I couldn't read that time. Use 24-hour HH:MM.")
        changes["local_time"] = local
    else:
        return Reply("For a meal, edit size or time.")
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
    chat_id: int | None,
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
    if looks_like_beads_request(text):
        try:
            intent_result = classify_beads_intent(text, client=client)
        except FeatureRequestNeedsClarification as exc:
            return _remember_beads_clarification(
                session, owner, chat_id, text, exc.question, now=now
            )
        except FeatureRequestRejected:
            return Reply(
                "I couldn't safely interpret that project request. Nothing was run or "
                "created. Use /bd-list, /bd-status, or /bd-add <feature request>."
            )
        except FeatureRequestEvaluationFailed:
            return Reply(
                "The local language model couldn't safely interpret that project request. "
                "Nothing was run or created. Use /bd-list, /bd-status, or /bd-add."
            )
        if intent_result.outcome is not ModelOutcome.OK or intent_result.intent is None:
            return Reply(
                "The local language model is unavailable, so I couldn't interpret that "
                "project request. Nothing was run or created. The deterministic commands "
                "/bd-list, /bd-status, and /bd-add still work."
            )
        intent = intent_result.intent
        match intent.operation:
            case "list":
                return _cmd_bd_operation(BeadsOperation.LIST, message_id=message_id, now=now)
            case "status":
                return _cmd_bd_operation(BeadsOperation.STATUS, message_id=message_id, now=now)
            case "add":
                return _cmd_bd_add(
                    session,
                    owner,
                    intent.feature_request or "",
                    message_id=message_id,
                    client=client,
                    limiter=None,
                    model_policy=None,
                    now=now,
                    chat_id=chat_id,
                )
            case "none":
                pass
    model_started = monotonic()
    result = extract(
        session,
        owner_id=owner.id,
        message=text,
        timezone=owner.default_timezone,
        now=now,
        client=client,
    )
    log.info(
        "telegram message routed",
        integration="telegram",
        route="telegram_model_extraction",
        outcome=result.outcome.value,
        latency_ms=int((monotonic() - model_started) * 1000),
        model_name=result.model_name,
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
    FlagCode.MISSING_VITAL_VALUE: (
        "a required blood-pressure, weight, or temperature value is missing"
    ),
    FlagCode.INVALID_VITAL_VALUE: "a blood-pressure, weight, or temperature value needs correction",
    FlagCode.INFERRED_TEMPERATURE_UNIT: (
        "you omitted the temperature unit, so it was inferred from the value; confirm it"
    ),
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
        "/edit <number> <field> <value>\n"
        "Dose fields: amount, unit, time, medication, category (regular or stress).\n"
        "Blood pressure: systolic, diastolic, pulse, position, time.\n"
        "Symptom: category (glucocorticoid, mineralocorticoid, postural, other, none).\n"
        "Weight: amount, unit, time."
        "\nTemperature: value, unit, time."
    )
    return reply


def _display_decimal(value: Decimal | None) -> str:
    """Render canonical decimal values without leaking storage scale to the owner."""
    if value is None:
        return "?"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _describe(candidate: ValidatedCandidate) -> str:
    when = candidate.local_time.strftime("%H:%M") if candidate.local_time else "time unknown"
    match candidate.type:
        case CandidateType.DOSE:
            amount = f"{_display_decimal(candidate.amount)} {candidate.unit or ''}".strip()
            category = (
                "Stress dose" if candidate.dose_category is DoseCategory.STRESS else "Regular dose"
            )
            return f"{category}: {amount} {candidate.medication_name or '?'} at {when}"
        case CandidateType.SYMPTOM:
            severity = (
                f" (severity {candidate.severity}/10)" if candidate.severity is not None else ""
            )
            category = (
                f" · category {candidate.symptom_tracking_category.value}"
                if candidate.symptom_tracking_category is not None
                else " · category not recorded"
            )
            return f"Symptom: {candidate.symptom_name}{severity}{category} at {when}"
        case CandidateType.DIARY:
            privacy = " · sensitive" if candidate.is_sensitive else ""
            return f"Diary: {(candidate.text or '')[:100]}{privacy} at {when}"
        case CandidateType.LIFE_EVENT:
            category = candidate.life_event_category or LifeEventCategory.OTHER
            privacy = " · sensitive" if candidate.is_sensitive else ""
            return (
                f"Life event ({category.value}): {(candidate.text or '')[:100]}{privacy} at {when}"
            )
        case CandidateType.BLOOD_PRESSURE:
            reading = f"{candidate.systolic_mmhg or '?'}/{candidate.diastolic_mmhg or '?'} mmHg"
            pulse = f", pulse {candidate.pulse_bpm} bpm" if candidate.pulse_bpm is not None else ""
            setting = candidate.measurement_setting.value
            position = (
                f" · {candidate.body_position.value}" if candidate.body_position is not None else ""
            )
            return f"Blood pressure: {reading}{pulse} · {setting}{position} at {when}"
        case CandidateType.WEIGHT:
            if candidate.weight_value is None or candidate.weight_unit is None:
                return f"Weight: value or unit missing at {when}"
            pounds = vitals.display_weight_lb(candidate.weight_value, candidate.weight_unit)
            entered = (
                f" (entered {candidate.weight_value} {candidate.weight_unit})"
                if candidate.weight_unit is WeightUnit.KG
                else ""
            )
            return f"Weight: {pounds} lb{entered} · {candidate.measurement_setting.value} at {when}"
        case CandidateType.TEMPERATURE:
            if candidate.temperature_value is None or candidate.temperature_unit is None:
                return f"Temperature: value or unit missing at {when}"
            fahrenheit = vitals.display_temperature_f(
                candidate.temperature_value, candidate.temperature_unit
            )
            celsius = vitals.display_temperature_c(
                candidate.temperature_value, candidate.temperature_unit
            )
            inferred = (
                f" · {candidate.temperature_unit.value.upper()} inferred from value"
                if FlagCode.INFERRED_TEMPERATURE_UNIT in candidate.flags
                else ""
            )
            return f"Temperature: {fahrenheit} °F ({celsius} °C){inferred} at {when}"
        case CandidateType.MEAL:
            size = f" · size {candidate.meal_size.value.upper()}" if candidate.meal_size else ""
            return f"Meal{size} at {when}"
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
    reminder = session.scalar(
        select(TelegramDoseReminder).where(
            TelegramDoseReminder.owner_id == owner.id,
            TelegramDoseReminder.draft_id == draft.id,
        )
    )
    if reminder is not None:
        reminder.state = DoseReminderState.SATISFIED
        reminder.resolved_at = draft.resolved_at
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
                category=candidate.dose_category or DoseCategory.SCHEDULED,
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
                tracking_category=candidate.symptom_tracking_category,
                tracking_category_revision=(
                    SYMPTOM_TRACKING_CATEGORY_REVISION
                    if candidate.symptom_tracking_category is not None
                    else None
                ),
                episode_id=open_episode.id if open_episode else None,
            )
        case CandidateType.DIARY:
            return events.create_event(
                session,
                DiaryEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                text=candidate.text or "",
                is_sensitive=candidate.is_sensitive,
            )
        case CandidateType.LIFE_EVENT:
            return events.create_event(
                session,
                LifeEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                title=candidate.text or "",
                category=candidate.life_event_category or LifeEventCategory.OTHER,
                description=None,
                is_sensitive=candidate.is_sensitive,
            )
        case CandidateType.BLOOD_PRESSURE:
            if candidate.systolic_mmhg is None or candidate.diastolic_mmhg is None:
                return None
            return events.create_event(
                session,
                BloodPressureEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                systolic_mmhg=candidate.systolic_mmhg,
                diastolic_mmhg=candidate.diastolic_mmhg,
                pulse_bpm=candidate.pulse_bpm,
                measurement_setting=candidate.measurement_setting,
                body_position=candidate.body_position,
            )
        case CandidateType.WEIGHT:
            if candidate.weight_value is None or candidate.weight_unit is None:
                return None
            return events.create_event(
                session,
                WeightEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                value=candidate.weight_value,
                unit=candidate.weight_unit,
                normalized_kg=vitals.normalize_weight_kg(
                    candidate.weight_value, candidate.weight_unit
                ),
                measurement_setting=candidate.measurement_setting,
            )
        case CandidateType.TEMPERATURE:
            if candidate.temperature_value is None or candidate.temperature_unit is None:
                return None
            return events.create_event(
                session,
                TemperatureEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                value=candidate.temperature_value,
                unit=candidate.temperature_unit,
                normalized_c=vitals.normalize_temperature_c(
                    candidate.temperature_value, candidate.temperature_unit
                ),
            )
        case CandidateType.MEAL:
            return events.create_event(
                session,
                MealEvent,
                owner_id=owner.id,
                event_time=event_time,
                source_type=SourceType.TELEGRAM,
                confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
                size=candidate.meal_size,
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
        reminder = session.scalar(
            select(TelegramDoseReminder).where(
                TelegramDoseReminder.owner_id == owner.id,
                TelegramDoseReminder.draft_id == draft.id,
            )
        )
        if reminder is not None:
            reminder.state = DoseReminderState.DISMISSED
            reminder.resolved_at = draft.resolved_at
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
    conversation.expire_contexts(session, now=now)
    return len(stale)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pending_draft(session: Session, owner_id: uuid.UUID) -> ExtractionDraft | None:
    return session.scalar(
        select(ExtractionDraft)
        .where(
            ExtractionDraft.owner_id == owner_id,
            ExtractionDraft.source.in_(
                (
                    "telegram",
                    "telegram_command",
                    "telegram_conversational",
                    "telegram_plan_shorthand",
                )
            ),
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
    return re.fullmatch(r"\s*\d{1,2}[:.]\d{2}\s*(?:am|pm)?\s*", token, re.IGNORECASE) is not None


def _parse_time_token(token: str, local_reference: datetime) -> datetime | None:
    """Parse ``HH:MM`` against today, rolling back a day if that would be the future."""
    match = re.fullmatch(
        r"\s*(?P<hour>\d{1,2})[:.](?P<minute>\d{2})\s*(?P<meridiem>am|pm)?\s*",
        token,
        re.IGNORECASE,
    )
    if match is None:
        return None
    hour, minute = int(match.group("hour")), int(match.group("minute"))
    meridiem = (match.group("meridiem") or "").lower()
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    candidate = local_reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > local_reference + timedelta(minutes=10):
        # "at 23:30" sent at 00:10 means last night, not tonight.
        candidate -= timedelta(days=1)
    return candidate


def today_local(owner: Owner, now: datetime) -> date:
    return _local_now(owner, now).date()
