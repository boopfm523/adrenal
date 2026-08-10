"""API schemas.

Every resource carries a ``category`` discriminator of ``fact``, ``plan``, or ``ai``
(SAFE-02). It is a literal on each response model rather than a field the caller can
set, so a fact can never be serialised claiming to be a plan.

Amounts cross the wire as strings. JSON numbers are IEEE-754 doubles, so serialising a
Decimal as a number would reintroduce exactly the float imprecision ADR-0001 forbids.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from healthcurve.episodes.models import EpisodeSeverity, EpisodeStatus
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import LifeEventCategory
from healthcurve.medications.models import (
    DoseCategory,
    DoseUnit,
    InstructionCategory,
    RegimenStatus,
    Route,
)
from healthcurve.vitals.models import WeightUnit

#: A positive clinical quantity. Bounded above so a typo cannot record 15000 mg.
Amount = Annotated[Decimal, Field(gt=0, le=10000, max_digits=10, decimal_places=4)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class FactResource(ApiModel):
    """Base for anything the owner reported, entered, or imported."""

    category: Literal["fact"] = "fact"


class PlanResource(ApiModel):
    """Base for physician-approved plan content."""

    category: Literal["plan"] = "plan"


class AiResource(ApiModel):
    """Base for generated content. Always labelled, always cited (SAFE-04, SAFE-05)."""

    category: Literal["ai"] = "ai"
    generated_at: datetime
    model_name: str
    prompt_version: str
    disclaimer: str = (
        "Generated analysis. Not medical advice, not a plan, and not a substitute for "
        "your physician-approved instructions."
    )


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


class EventTimeIn(ApiModel):
    """When something happened, as the owner experienced it."""

    local_time: datetime = Field(description="Naive local wall time, e.g. 2026-03-29T07:08:00")
    timezone: str = Field(description="IANA zone, e.g. Europe/London")
    fold: int | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Only for a local time repeated by a DST fall-back. 0 selects the first "
            "occurrence, 1 the second. Omitting it on an ambiguous time is an error "
            "rather than a guess."
        ),
    )


class EventTimeOut(ApiModel):
    occurred_at: datetime
    local_time: datetime
    timezone: str
    utc_offset_minutes: int


class ProvenanceOut(ApiModel):
    """How this record came to exist, and whether it has been corrected."""

    recorded_at: datetime
    source_type: SourceType
    confirmation_state: ConfirmationState
    supersedes_id: uuid.UUID | None = None
    correction_reason: str | None = None
    is_correction: bool = False


# ---------------------------------------------------------------------------
# Medications (plan)
# ---------------------------------------------------------------------------


class MedicationIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    formulation: str | None = Field(default=None, max_length=120)
    strength: Amount | None = None
    strength_unit: str | None = Field(default=None, max_length=16)
    default_unit: DoseUnit
    default_route: Route = Route.ORAL
    active_from: date | None = None
    active_to: date | None = None
    notes: str | None = None


class MedicationOut(PlanResource):
    id: uuid.UUID
    name: str
    formulation: str | None
    strength: Decimal | None
    strength_unit: str | None
    default_unit: DoseUnit
    default_route: Route
    active_from: date | None
    active_to: date | None
    notes: str | None

    @field_serializer("strength")
    def _strength(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class DoseSlotIn(ApiModel):
    medication_id: uuid.UUID
    scheduled_local_time: time
    amount: Amount
    unit: DoseUnit
    route: Route = Route.ORAL
    condition: str | None = Field(default=None, max_length=500)
    sort_order: int = 0


class DoseSlotOut(PlanResource):
    id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    scheduled_local_time: time
    amount: Decimal
    unit: DoseUnit
    route: Route
    condition: str | None
    sort_order: int

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return str(value)


class InstructionIn(ApiModel):
    category: InstructionCategory
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    authored_by: str = Field(min_length=1, max_length=200)
    authored_on: date
    sort_order: int = 0


class InstructionOut(PlanResource):
    id: uuid.UUID
    instruction_category: InstructionCategory
    title: str
    body: str
    authored_by: str
    authored_on: date
    sort_order: int


class RegimenVersionIn(ApiModel):
    version_label: str = Field(min_length=1, max_length=60)
    effective_from: datetime
    effective_to: datetime | None = None
    notes: str | None = None
    slots: list[DoseSlotIn] = Field(default_factory=list)
    instructions: list[InstructionIn] = Field(default_factory=list)


class RegimenApprovalIn(ApiModel):
    """Approval is a human act with provenance (SAFE-16). None of this is optional."""

    approved_by: str = Field(min_length=1, max_length=200, description="Clinician name or role")
    approval_source: str = Field(
        min_length=1, max_length=200, description="Letter, consultation, portal message"
    )
    approved_at: datetime | None = None
    source_document_checksum: str | None = Field(default=None, max_length=128)


class RegimenVersionOut(PlanResource):
    id: uuid.UUID
    version_label: str
    status: RegimenStatus
    effective_from: datetime
    effective_to: datetime | None
    approved_at: datetime | None
    approved_by: str | None
    approval_source: str | None
    retired_at: datetime | None
    notes: str | None
    slots: list[DoseSlotOut] = Field(default_factory=list)
    instructions: list[InstructionOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Doses (fact)
# ---------------------------------------------------------------------------


class DoseIn(ApiModel):
    medication_id: uuid.UUID
    amount: Amount
    unit: DoseUnit
    route: Route = Route.ORAL
    category: DoseCategory = DoseCategory.SCHEDULED
    time: EventTimeIn
    slot_id: uuid.UUID | None = None
    episode_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class DoseOut(FactResource):
    id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    amount: Decimal
    unit: DoseUnit
    route: Route
    dose_category: DoseCategory
    time: EventTimeOut
    provenance: ProvenanceOut
    regimen_version_id: uuid.UUID | None
    slot_id: uuid.UUID | None
    episode_id: uuid.UUID | None
    notes: str | None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return str(value)


class DoseCorrectionChanges(ApiModel):
    """Fields an owner may correct on a recorded dose."""

    amount: Amount | None = None
    unit: DoseUnit | None = None
    route: Route | None = None
    category: DoseCategory | None = None
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> DoseCorrectionChanges:
        nullable_only = {"notes"}
        null_fields = {
            name for name in self.model_fields_set - nullable_only if getattr(self, name) is None
        }
        if null_fields:
            raise ValueError(f"correction field(s) cannot be null: {sorted(null_fields)}")
        return self


class DoseCorrectionIn(ApiModel):
    """A typed dose correction; omitted fields are copied from the prior fact."""

    reason: str = Field(min_length=1, max_length=500)
    changes: DoseCorrectionChanges


# ---------------------------------------------------------------------------
# Blood pressure and weight (fact)
# ---------------------------------------------------------------------------


class BloodPressureIn(ApiModel):
    systolic_mmhg: int = Field(ge=1, le=500)
    diastolic_mmhg: int = Field(ge=1, le=500)
    pulse_bpm: int | None = Field(default=None, ge=1, le=500)
    time: EventTimeIn
    notes: str | None = Field(default=None, max_length=2000)


class BloodPressureOut(FactResource):
    id: uuid.UUID
    systolic_mmhg: int
    diastolic_mmhg: int
    pulse_bpm: int | None
    time: EventTimeOut
    provenance: ProvenanceOut
    notes: str | None


class BloodPressureCorrectionChanges(ApiModel):
    systolic_mmhg: int | None = Field(default=None, ge=1, le=500)
    diastolic_mmhg: int | None = Field(default=None, ge=1, le=500)
    pulse_bpm: int | None = Field(default=None, ge=1, le=500)
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> BloodPressureCorrectionChanges:
        nullable = {"pulse_bpm", "notes"}
        null_fields = {
            name for name in self.model_fields_set - nullable if getattr(self, name) is None
        }
        if null_fields:
            raise ValueError(f"correction field(s) cannot be null: {sorted(null_fields)}")
        return self


class BloodPressureCorrectionIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    changes: BloodPressureCorrectionChanges


WeightValue = Annotated[Decimal, Field(gt=0, le=5000, max_digits=10, decimal_places=4)]


class WeightIn(ApiModel):
    value: WeightValue
    unit: WeightUnit
    time: EventTimeIn
    notes: str | None = Field(default=None, max_length=2000)


class WeightOut(FactResource):
    id: uuid.UUID
    value: Decimal
    unit: WeightUnit
    normalized_kg: Decimal
    time: EventTimeOut
    provenance: ProvenanceOut
    notes: str | None

    @field_serializer("value", "normalized_kg")
    def _decimal(self, value: Decimal) -> str:
        return format(value, ".4f")


class WeightCorrectionChanges(ApiModel):
    value: WeightValue | None = None
    unit: WeightUnit | None = None
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> WeightCorrectionChanges:
        nullable = {"notes"}
        null_fields = {
            name for name in self.model_fields_set - nullable if getattr(self, name) is None
        }
        if null_fields:
            raise ValueError(f"correction field(s) cannot be null: {sorted(null_fields)}")
        return self


class WeightCorrectionIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    changes: WeightCorrectionChanges


# ---------------------------------------------------------------------------
# Laboratory results (fact plus explicitly derived normalization)
# ---------------------------------------------------------------------------


class LabResultOut(FactResource):
    id: uuid.UUID
    panel_id: uuid.UUID
    source_document_id: uuid.UUID | None
    source_page_number: int | None
    analyte_name: str
    original_value: str | None
    qualitative_result: str | None
    original_unit: str | None
    original_reference_range: str | None
    abnormal_flag: str | None
    normalized_analyte_code: str | None
    normalized_analyte_name: str | None
    normalized_value: Decimal | None
    normalized_unit: str | None
    normalization_method: str | None
    specimen_time: EventTimeOut
    specimen_type: str | None
    laboratory_name: str | None
    source_type: SourceType
    confirmation_state: ConfirmationState

    @field_serializer("normalized_value")
    def _normalized_value(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


# ---------------------------------------------------------------------------
# Other events (fact)
# ---------------------------------------------------------------------------


class SymptomIn(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    severity: int | None = Field(default=None, ge=0, le=10)
    body_area: str | None = Field(default=None, max_length=120)
    time: EventTimeIn
    ended_at: datetime | None = None
    episode_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SymptomOut(FactResource):
    id: uuid.UUID
    name: str
    severity: int | None
    body_area: str | None
    time: EventTimeOut
    provenance: ProvenanceOut
    episode_id: uuid.UUID | None
    notes: str | None


class SymptomCorrectionChanges(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    severity: int | None = Field(default=None, ge=0, le=10)
    body_area: str | None = Field(default=None, max_length=120)
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> SymptomCorrectionChanges:
        nullable_fields = {"severity", "body_area", "notes"}
        null_fields = {
            name for name in self.model_fields_set - nullable_fields if getattr(self, name) is None
        }
        if null_fields:
            raise ValueError(f"correction field(s) cannot be null: {sorted(null_fields)}")
        return self


class SymptomCorrectionIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    changes: SymptomCorrectionChanges


class DiaryIn(ApiModel):
    text: str = Field(min_length=1, max_length=10_000)
    is_sensitive: bool = False
    tags: str | None = Field(default=None, max_length=500)
    time: EventTimeIn


class DiaryOut(FactResource):
    id: uuid.UUID
    text: str
    is_sensitive: bool
    tags: str | None
    time: EventTimeOut
    provenance: ProvenanceOut


class LifeEventIn(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    category: LifeEventCategory
    description: str | None = None
    time: EventTimeIn
    ended_at: datetime | None = None
    is_sensitive: bool = False


class LifeEventOut(FactResource):
    id: uuid.UUID
    title: str
    life_category: LifeEventCategory
    description: str | None
    is_sensitive: bool
    time: EventTimeOut
    provenance: ProvenanceOut


# ---------------------------------------------------------------------------
# Episodes and injections (fact)
# ---------------------------------------------------------------------------


class EpisodeIn(ApiModel):
    trigger: str = Field(min_length=1, max_length=200)
    severity: EpisodeSeverity | None = None
    time: EventTimeIn
    highest_temperature_c: Decimal | None = Field(default=None, ge=25, le=45)
    illness_description: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class EpisodeUpdate(ApiModel):
    severity: EpisodeSeverity | None = None
    status: EpisodeStatus | None = None
    ended_at: EventTimeIn | None = None
    highest_temperature_c: Decimal | None = Field(default=None, ge=25, le=45)
    illness_description: str | None = Field(default=None, max_length=500)
    recovery_notes: str | None = None
    outcome: str | None = None
    notes: str | None = None


class EpisodeOut(FactResource):
    id: uuid.UUID
    trigger: str
    status: EpisodeStatus
    severity: EpisodeSeverity | None
    started_at: datetime
    ended_at: datetime | None
    timezone: str
    highest_temperature_c: Decimal | None
    illness_description: str | None
    recovery_notes: str | None
    outcome: str | None
    notes: str | None
    dose_count: int = 0
    symptom_count: int = 0


class InjectionIn(ApiModel):
    """Emergency injection. Only the clinical essentials are required (SAFE-23)."""

    medication_id: uuid.UUID
    amount: Amount
    unit: DoseUnit = DoseUnit.MG
    route: Route = Route.INTRAMUSCULAR
    time: EventTimeIn
    injection_site: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=500)
    injected_by: str | None = Field(default=None, max_length=120)
    response: str | None = None
    emergency_services_called: bool | None = None
    transported_to_hospital: bool | None = None
    contact_notified: str | None = Field(default=None, max_length=200)
    episode_id: uuid.UUID | None = None


class InjectionOut(FactResource):
    id: uuid.UUID
    medication_id: uuid.UUID
    amount: Decimal
    unit: str
    route: str
    time: EventTimeOut
    provenance: ProvenanceOut
    injection_site: str | None
    reason: str | None
    injected_by: str | None
    response: str | None
    emergency_services_called: bool | None
    transported_to_hospital: bool | None
    episode_id: uuid.UUID | None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return str(value)


# ---------------------------------------------------------------------------
# Timeline and comparison
# ---------------------------------------------------------------------------


class TimelineItem(ApiModel):
    """One entry on the unified timeline.

    ``category`` is per item, never per collection: the timeline mixes facts and plan
    context, and SAFE-02 requires each to say which it is.
    """

    id: uuid.UUID
    category: Literal["fact", "plan", "ai"]
    event_type: str
    summary: str
    time: EventTimeOut
    provenance: ProvenanceOut | None = None
    is_sensitive: bool = False


class TimelinePage(ApiModel):
    items: list[TimelineItem]
    next_cursor: str | None = None
    #: Stated so a reader knows which zone the day boundaries were drawn in (SAFE-27).
    timezone: str


class PlanComparisonSlot(ApiModel):
    slot_id: uuid.UUID | None
    medication_id: uuid.UUID
    medication_name: str
    scheduled_local_time: time | None
    planned_amount: Decimal | None
    actual_amount: Decimal | None
    actual_local_time: datetime | None
    dose_id: uuid.UUID | None
    #: on_time | late | early | missing | unplanned | extra
    status: str
    minutes_from_scheduled: int | None
    unit: DoseUnit
    route: Route

    @field_serializer("planned_amount", "actual_amount")
    def _amounts(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class PlanComparisonDay(ApiModel):
    """A day compared against the plan in force on that day.

    ``missing`` slots are *derived*, not stored: no zero-dose row is ever written
    (SAFE-10).
    """

    date: date
    timezone: str
    regimen_version_id: uuid.UUID | None
    regimen_version_label: str | None
    slots: list[PlanComparisonSlot]
    planned_total: Decimal | None
    actual_total: Decimal
    unplanned_doses: int
    missed_slots: int
    #: Rendered beside every figure (SAFE-27).
    metric_definition: str

    @field_serializer("planned_total", "actual_total")
    def _totals(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Deterministic analytics
# ---------------------------------------------------------------------------


class MetricBase(ApiModel):
    definition: str
    timezone: str
    sample_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)


class DailyDoseValue(ApiModel):
    date: date
    planned_total: Decimal | None
    actual_total: Decimal | None
    recorded_dose_count: int = Field(ge=0)
    unit: str | None
    incompatible_units: bool

    @field_serializer("planned_total", "actual_total")
    def _totals(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class DailyDoseMetric(MetricBase):
    days_without_approved_plan: int = Field(ge=0)
    values: list[DailyDoseValue]


class TimingMetric(MetricBase):
    on_time: int = Field(ge=0)
    early: int = Field(ge=0)
    late: int = Field(ge=0)
    unplanned: int = Field(ge=0)


class EpisodeMetric(MetricBase):
    count: int = Field(ge=0)
    total_duration_minutes: Decimal
    average_duration_minutes: Decimal | None

    @field_serializer("total_duration_minutes")
    def _total_duration(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("average_duration_minutes")
    def _average_duration(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class SymptomMetric(MetricBase):
    count: int = Field(ge=0)
    average_severity: Decimal | None
    frequency: dict[str, int]

    @field_serializer("average_severity")
    def _severity(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class AnalyticsSummaryOut(ApiModel):
    date_from: date
    date_to: date
    timezone: str
    daily_doses: DailyDoseMetric
    timing: TimingMetric
    episodes: EpisodeMetric
    symptoms: SymptomMetric
