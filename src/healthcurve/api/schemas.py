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
from healthcurve.events.models import LifeEventCategory, MealSize, SymptomTrackingCategory
from healthcurve.integrations.garmin.models import GarminMetricType
from healthcurve.medications.models import (
    DoseCategory,
    DoseTimingMode,
    DoseUnit,
    InstructionCategory,
    RegimenStatus,
    Route,
)
from healthcurve.vitals.models import BodyPosition, MeasurementSetting, TemperatureUnit, WeightUnit

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


class PageMetadata(ApiModel):
    """Shared bounded pagination state for growing owner-scoped collections."""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=1)


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
    timing_mode: DoseTimingMode = DoseTimingMode.FIXED_TIME
    scheduled_local_time: time | None = None
    reminder_local_time: time | None = None
    amount: Amount
    unit: DoseUnit
    route: Route = Route.ORAL
    condition: str | None = Field(default=None, max_length=500)
    sort_order: int = 0

    @model_validator(mode="after")
    def _timing_fields_match_mode(self) -> DoseSlotIn:
        if self.timing_mode is DoseTimingMode.FIXED_TIME:
            if self.scheduled_local_time is None:
                raise ValueError("a fixed-time slot requires scheduled_local_time")
            if self.reminder_local_time is not None:
                raise ValueError("a fixed-time slot cannot set reminder_local_time")
        elif self.scheduled_local_time is not None or self.reminder_local_time is None:
            raise ValueError(
                "a wake-anchored slot requires reminder_local_time and cannot set "
                "scheduled_local_time"
            )
        return self


class DoseSlotOut(PlanResource):
    id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    timing_mode: DoseTimingMode
    scheduled_local_time: time | None
    reminder_local_time: time | None
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
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    effective_timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA timezone for naive effective wall times; owner default when omitted",
    )
    effective_from_fold: int | None = Field(default=None, ge=0, le=1)
    effective_to_fold: int | None = Field(default=None, ge=0, le=1)
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
    activation_local_time: datetime | None = Field(
        default=None,
        description=(
            "Optional owner-selected wall time for this plan to start. When omitted, "
            "the saved draft start is used, or the approval instant when the draft is undated."
        ),
    )
    activation_timezone: str | None = Field(default=None, max_length=64)
    activation_fold: int | None = Field(default=None, ge=0, le=1)


class RegimenVersionOut(PlanResource):
    id: uuid.UUID
    version_label: str
    status: RegimenStatus
    effective_from: datetime | None
    effective_to: datetime | None
    effective_timezone: str | None
    effective_from_local: datetime | None
    effective_to_local: datetime | None
    effective_from_utc_offset_minutes: int | None
    effective_to_utc_offset_minutes: int | None
    effective_time_provenance: str
    approved_at: datetime | None
    approved_by: str | None
    approval_source: str | None
    retired_at: datetime | None
    notes: str | None
    deletion_allowed: bool
    slots: list[DoseSlotOut] = Field(default_factory=list)
    instructions: list[InstructionOut] = Field(default_factory=list)


class RegimenVersionPage(ApiModel):
    items: list[RegimenVersionOut]
    page: PageMetadata


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
    formulation: str | None
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

    medication_id: uuid.UUID | None = None
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
# Blood pressure, weight, and temperature (facts)
# ---------------------------------------------------------------------------


class BloodPressureIn(ApiModel):
    systolic_mmhg: int = Field(ge=1, le=500)
    diastolic_mmhg: int = Field(ge=1, le=500)
    pulse_bpm: int | None = Field(default=None, ge=1, le=500)
    measurement_setting: MeasurementSetting = MeasurementSetting.HOME
    body_position: BodyPosition | None = None
    time: EventTimeIn
    notes: str | None = Field(default=None, max_length=2000)


class BloodPressureOut(FactResource):
    id: uuid.UUID
    systolic_mmhg: int
    diastolic_mmhg: int
    pulse_bpm: int | None
    measurement_setting: MeasurementSetting
    body_position: BodyPosition | None
    time: EventTimeOut
    provenance: ProvenanceOut
    notes: str | None


class BloodPressureCorrectionChanges(ApiModel):
    systolic_mmhg: int | None = Field(default=None, ge=1, le=500)
    diastolic_mmhg: int | None = Field(default=None, ge=1, le=500)
    pulse_bpm: int | None = Field(default=None, ge=1, le=500)
    measurement_setting: MeasurementSetting | None = None
    body_position: BodyPosition | None = None
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> BloodPressureCorrectionChanges:
        nullable = {"pulse_bpm", "body_position", "notes"}
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
    measurement_setting: MeasurementSetting = MeasurementSetting.HOME
    time: EventTimeIn
    notes: str | None = Field(default=None, max_length=2000)


class WeightOut(FactResource):
    id: uuid.UUID
    value: Decimal
    unit: WeightUnit
    normalized_kg: Decimal
    display_lb: Decimal
    measurement_setting: MeasurementSetting
    time: EventTimeOut
    provenance: ProvenanceOut
    notes: str | None

    @field_serializer("value", "normalized_kg")
    def _decimal(self, value: Decimal) -> str:
        return format(value, ".4f")

    @field_serializer("display_lb")
    def _display_lb(self, value: Decimal) -> str:
        return format(value, ".1f")


class WeightCorrectionChanges(ApiModel):
    value: WeightValue | None = None
    unit: WeightUnit | None = None
    measurement_setting: MeasurementSetting | None = None
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


TemperatureValue = Annotated[Decimal, Field(max_digits=6, decimal_places=2)]


class TemperatureIn(ApiModel):
    value: TemperatureValue
    unit: TemperatureUnit
    time: EventTimeIn
    notes: str | None = Field(default=None, max_length=2000)


class TemperatureOut(FactResource):
    id: uuid.UUID
    value: Decimal
    unit: TemperatureUnit
    normalized_c: Decimal
    display_f: Decimal
    display_c: Decimal
    time: EventTimeOut
    provenance: ProvenanceOut
    notes: str | None

    @field_serializer("value", "normalized_c")
    def _decimal(self, value: Decimal) -> str:
        return format(value, ".2f")

    @field_serializer("display_f", "display_c")
    def _display(self, value: Decimal) -> str:
        return format(value, ".1f")


class TemperatureCorrectionChanges(ApiModel):
    value: TemperatureValue | None = None
    unit: TemperatureUnit | None = None
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> TemperatureCorrectionChanges:
        nullable = {"notes"}
        null_fields = {
            name for name in self.model_fields_set - nullable if getattr(self, name) is None
        }
        if null_fields:
            raise ValueError(f"correction field(s) cannot be null: {sorted(null_fields)}")
        return self


class TemperatureCorrectionIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    changes: TemperatureCorrectionChanges


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


class LabResultPage(ApiModel):
    items: list[LabResultOut]
    page: PageMetadata


# ---------------------------------------------------------------------------
# Other events (fact)
# ---------------------------------------------------------------------------


class SymptomIn(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    severity: int | None = Field(default=None, ge=0, le=10)
    body_area: str | None = Field(default=None, max_length=120)
    tracking_category: SymptomTrackingCategory | None = Field(
        default=None,
        description="Optional owner-selected correlation context; not a diagnosis",
    )
    time: EventTimeIn
    ended_at: datetime | None = None
    episode_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SymptomOut(FactResource):
    id: uuid.UUID
    name: str
    severity: int | None
    body_area: str | None
    tracking_category: SymptomTrackingCategory | None
    tracking_category_revision: str | None
    time: EventTimeOut
    provenance: ProvenanceOut
    episode_id: uuid.UUID | None
    notes: str | None


class DosePage(ApiModel):
    items: list[DoseOut]
    revisions: list[DoseOut]
    page: PageMetadata


class BloodPressurePage(ApiModel):
    items: list[BloodPressureOut]
    revisions: list[BloodPressureOut]
    page: PageMetadata


class WeightPage(ApiModel):
    items: list[WeightOut]
    revisions: list[WeightOut]
    page: PageMetadata


class TemperaturePage(ApiModel):
    items: list[TemperatureOut]
    revisions: list[TemperatureOut]
    page: PageMetadata


class SymptomPage(ApiModel):
    items: list[SymptomOut]
    revisions: list[SymptomOut]
    page: PageMetadata


class SymptomCorrectionChanges(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    severity: int | None = Field(default=None, ge=0, le=10)
    body_area: str | None = Field(default=None, max_length=120)
    tracking_category: SymptomTrackingCategory | None = None
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _required_values_cannot_be_null(self) -> SymptomCorrectionChanges:
        nullable_fields = {"severity", "body_area", "tracking_category", "notes"}
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


class DiaryPage(ApiModel):
    items: list[DiaryOut]
    revisions: list[DiaryOut]
    page: PageMetadata


class MealIn(ApiModel):
    size: MealSize | None = None
    time: EventTimeIn
    notes: str | None = Field(default=None, max_length=2000)


class MealOut(FactResource):
    id: uuid.UUID
    size: MealSize | None
    time: EventTimeOut
    provenance: ProvenanceOut
    notes: str | None


class MealPage(ApiModel):
    items: list[MealOut]
    revisions: list[MealOut]
    page: PageMetadata


class MealCorrectionChanges(ApiModel):
    size: MealSize | None = None
    time: EventTimeIn | None = None
    notes: str | None = Field(default=None, max_length=2000)


class MealCorrectionIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    changes: MealCorrectionChanges


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


class LifeEventPage(ApiModel):
    items: list[LifeEventOut]
    revisions: list[LifeEventOut]
    page: PageMetadata


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


class EpisodePage(ApiModel):
    items: list[EpisodeOut]
    page: PageMetadata


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


class InjectionPage(ApiModel):
    items: list[InjectionOut]
    revisions: list[InjectionOut]
    page: PageMetadata


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
    page: PageMetadata
    #: Stated so a reader knows which zone the day boundaries were drawn in (SAFE-27).
    timezone: str


class PlanComparisonSlot(ApiModel):
    slot_id: uuid.UUID | None
    medication_id: uuid.UUID
    medication_name: str
    timing_mode: DoseTimingMode | None
    scheduled_local_time: time | None
    reminder_local_time: time | None
    planned_amount: Decimal | None
    actual_amount: Decimal | None
    actual_local_time: datetime | None
    dose_id: uuid.UUID | None
    #: on_time | late | early | recorded | missing | unplanned | extra
    status: str
    minutes_from_scheduled: int | None
    absolute_minutes_from_scheduled: int | None
    regimen_version_id: uuid.UUID | None
    regimen_version_label: str | None
    regimen_effective_from: datetime | None
    regimen_effective_to: datetime | None
    unit: DoseUnit
    route: Route

    @field_serializer("planned_amount", "actual_amount")
    def _amounts(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class PlanComparisonRegimen(ApiModel):
    id: uuid.UUID
    version_label: str
    effective_from: datetime
    effective_to: datetime | None


class PlanComparisonDay(ApiModel):
    """A day compared against the historical plan intervals in force on that day.

    ``missing`` slots are *derived*, not stored: no zero-dose row is ever written
    (SAFE-10).
    """

    date: date
    timezone: str
    regimen_version_id: uuid.UUID | None
    regimen_version_label: str | None
    regimen_versions: list[PlanComparisonRegimen]
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
    matched_count: int = Field(ge=0)
    on_time: int = Field(ge=0)
    early: int = Field(ge=0)
    late: int = Field(ge=0)
    unplanned: int = Field(ge=0)
    total_absolute_deviation_minutes: Decimal | None
    average_absolute_deviation_minutes: Decimal | None
    plan_periods: list[TimingPlanPeriod]

    @field_serializer("total_absolute_deviation_minutes", "average_absolute_deviation_minutes")
    def _deviation(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class TimingPlanPeriod(ApiModel):
    regimen_version_id: uuid.UUID | None
    regimen_version_label: str | None
    effective_from: datetime | None
    effective_to: datetime | None
    sample_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    on_time: int = Field(ge=0)
    early: int = Field(ge=0)
    late: int = Field(ge=0)
    unplanned: int = Field(ge=0)
    total_absolute_deviation_minutes: Decimal | None
    average_absolute_deviation_minutes: Decimal | None

    @field_serializer("total_absolute_deviation_minutes", "average_absolute_deviation_minutes")
    def _deviation(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


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


class SteroidExposureModelOut(ApiModel):
    version: str
    supported_medication: str
    supported_formulation: str
    supported_route: Route
    amount_unit: DoseUnit
    absorption_rate_per_hour: Decimal
    elimination_half_life_hours: Decimal
    elimination_rate_per_hour: Decimal
    peak_time_hours: Decimal
    contribution_horizon_hours: int = Field(gt=0)
    sample_interval_minutes: int = Field(gt=0)
    references: list[str]

    @field_serializer(
        "absorption_rate_per_hour",
        "elimination_half_life_hours",
        "elimination_rate_per_hour",
        "peak_time_hours",
    )
    def _parameters(self, value: Decimal) -> str:
        return str(value)


class SteroidExposureDoseMarker(ApiModel):
    dose_event_id: uuid.UUID
    occurred_at: datetime
    local_time: datetime
    timezone: str
    utc_offset_minutes: int
    medication_name: str
    formulation: str | None
    amount: Decimal
    unit: DoseUnit
    route: Route
    category: DoseCategory
    source_type: SourceType
    confirmation_state: ConfirmationState
    supersedes_id: uuid.UUID | None
    supported: bool
    exclusion_reason: (
        Literal[
            "unsupported_medication",
            "unsupported_formulation",
            "unsupported_route",
            "unsupported_unit",
            "unsupported_amount",
        ]
        | None
    )
    carryover: bool
    modeled_peak_at: datetime | None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> str:
        return str(value)


class SteroidExposureSample(ApiModel):
    occurred_at: datetime
    local_time: datetime
    utc_offset_minutes: int
    theoretical_exposure_reu: Decimal = Field(ge=0)
    regular_exposure_reu: Decimal = Field(ge=0)
    stress_exposure_reu: Decimal = Field(ge=0)

    @field_serializer("theoretical_exposure_reu", "regular_exposure_reu", "stress_exposure_reu")
    def _exposure(self, value: Decimal) -> str:
        return str(value)


class SteroidExposureCurveOut(ApiModel):
    date: date
    timezone: str
    day_start: datetime
    day_end: datetime
    elapsed_hours: Decimal
    series_name: Literal["Theoretical hydrocortisone exposure"]
    series_unit: Literal["REU"]
    safety_label: str
    definition: str
    model: SteroidExposureModelOut
    dose_markers: list[SteroidExposureDoseMarker]
    samples: list[SteroidExposureSample]
    supported_dose_count: int = Field(ge=0)
    excluded_dose_count: int = Field(ge=0)
    context_band: CircadianContextBandOut

    @field_serializer("elapsed_hours")
    def _elapsed(self, value: Decimal) -> str:
        return str(value)


class PhysiologicalCortisolModelOut(ApiModel):
    id: Literal["hc-physiology-v2"]
    revision: Literal["hc-physiology-v2.0.0"]
    supported_medication: str
    supported_formulation: str
    supported_route: Route
    amount_unit: DoseUnit
    absorption_rate_per_hour: Decimal
    oral_bioavailability: Decimal
    clearance_liters_per_hour: Decimal
    distribution_volume_liters: Decimal
    cortisol_molecular_weight: Decimal
    elimination_half_life_hours: Decimal
    elimination_rate_per_hour: Decimal
    peak_time_hours: Decimal
    contribution_horizon_hours: int = Field(gt=0)
    sample_interval_minutes: int = Field(gt=0)
    references: list[str]

    @field_serializer(
        "absorption_rate_per_hour",
        "oral_bioavailability",
        "clearance_liters_per_hour",
        "distribution_volume_liters",
        "cortisol_molecular_weight",
        "elimination_half_life_hours",
        "elimination_rate_per_hour",
        "peak_time_hours",
    )
    def _parameters(self, value: Decimal) -> str:
        return str(value)


class PhysiologicalCortisolSampleOut(ApiModel):
    occurred_at: datetime
    local_time: datetime
    utc_offset_minutes: int
    modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    regular_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    stress_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)

    @field_serializer(
        "modeled_free_cortisol_nmol_l",
        "regular_modeled_free_cortisol_nmol_l",
        "stress_modeled_free_cortisol_nmol_l",
    )
    def _values(self, value: Decimal) -> str:
        return str(value)


class CircadianContextSampleOut(ApiModel):
    occurred_at: datetime
    local_time: datetime
    utc_offset_minutes: int
    center_nmol_l: Decimal = Field(ge=0)
    lower_nmol_l: Decimal = Field(ge=0)
    upper_nmol_l: Decimal = Field(ge=0)

    @field_serializer("center_nmol_l", "lower_nmol_l", "upper_nmol_l")
    def _values(self, value: Decimal) -> str:
        return str(value)


class CircadianAnchorOut(ApiModel):
    local_hour: Decimal = Field(ge=0, le=24)
    center_nmol_l: Decimal = Field(ge=0)

    @field_serializer("local_hour", "center_nmol_l")
    def _values(self, value: Decimal) -> str:
        return str(value)


class CircadianContextModelOut(ApiModel):
    id: Literal["hc-circadian-context-v1"]
    revision: Literal["hc-circadian-context-v1.0.0"]
    interpolation: Literal["pchip-no-overshoot"]
    lower_multiplier: Decimal
    upper_multiplier: Decimal
    anchor_origin: Literal["owner_supplied_synthetic_scenario"]
    healthy_rhythm_evidence_scope: Literal["shape_and_phase_context_only"]
    personalized: Literal[False]
    body_context_used: Literal[False]
    demographic_reference_interval: Literal[False]
    references: list[str]
    anchors: list[CircadianAnchorOut]

    @field_serializer("lower_multiplier", "upper_multiplier")
    def _multipliers(self, value: Decimal) -> str:
        return str(value)


class RecordedStressContextOut(ApiModel):
    episode_count: int = Field(ge=0)
    missing_severity_count: int = Field(ge=0)
    multiplier: Decimal
    applied_to_band: Literal[False]
    applied_to_drug_model: Literal[False]
    reason: str

    @field_serializer("multiplier")
    def _multiplier(self, value: Decimal) -> str:
        return str(value)


class CircadianContextBandOut(ApiModel):
    date: date
    timezone: str
    day_start: datetime
    day_end: datetime
    elapsed_hours: Decimal
    series_kind: Literal["illustrative_circadian_context_band"]
    series_name: Literal["Illustrative circadian context band"]
    series_unit: Literal["nmol/L"]
    default_visible: Literal[False]
    safety_label: str
    band: CircadianContextModelOut
    recorded_stress_context: RecordedStressContextOut
    samples: list[CircadianContextSampleOut]

    @field_serializer("elapsed_hours")
    def _elapsed(self, value: Decimal) -> str:
        return str(value)


class PhysiologicalCortisolCurveOut(ApiModel):
    date: date
    timezone: str
    day_start: datetime
    day_end: datetime
    elapsed_hours: Decimal
    series_kind: Literal["modeled_plasma_free_cortisol_scenario"]
    series_name: Literal["Modeled plasma-free-cortisol scenario"]
    series_unit: Literal["nmol/L"]
    safety_label: str
    definition: str
    model: PhysiologicalCortisolModelOut
    source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dose_markers: list[SteroidExposureDoseMarker]
    samples: list[PhysiologicalCortisolSampleOut]
    supported_dose_count: int = Field(ge=0)
    excluded_dose_count: int = Field(ge=0)
    context_band: CircadianContextBandOut

    @field_serializer("elapsed_hours")
    def _elapsed(self, value: Decimal) -> str:
        return str(value)


class WakeFreePkParametersIn(ApiModel):
    elimination_half_life_hours: Decimal = Field(ge=Decimal("0.25"), le=Decimal("12"))
    peak_time_hours: Decimal = Field(ge=Decimal("0.1"), le=Decimal("8"))
    distribution_volume_liters: Decimal = Field(ge=Decimal("1"), le=Decimal("500"))
    oral_bioavailability: Decimal = Field(gt=0, le=1)


class WakeFreePkParametersOut(ApiModel):
    revision_id: uuid.UUID | None
    revision_number: int = Field(ge=0)
    population_default: bool
    created_at: datetime | None
    source_revision: str
    elimination_half_life_hours: Decimal
    peak_time_hours: Decimal
    distribution_volume_liters: Decimal
    oral_bioavailability: Decimal
    absorption_rate_per_hour: Decimal
    elimination_rate_per_hour: Decimal
    derived_clearance_liters_per_hour: Decimal

    @field_serializer(
        "elimination_half_life_hours",
        "peak_time_hours",
        "distribution_volume_liters",
        "oral_bioavailability",
        "absorption_rate_per_hour",
        "elimination_rate_per_hour",
        "derived_clearance_liters_per_hour",
    )
    def _parameters(self, value: Decimal) -> str:
        return str(value)


class WakeFreePkReferenceDefaultsOut(ApiModel):
    absorption_duration_hours: Decimal
    clearance_liters_per_hour: Decimal
    free_peak_10_mg_nmol_l: Decimal
    calibration_revision: str

    @field_serializer(
        "absorption_duration_hours",
        "clearance_liters_per_hour",
        "free_peak_10_mg_nmol_l",
    )
    def _parameters(self, value: Decimal) -> str:
        return str(value)


class WakeFreePkSettingsOut(ApiModel):
    model_id: Literal["hc-wake-free-v3"]
    model_revision: Literal["hc-wake-free-v3.0.0"]
    parameters: WakeFreePkParametersOut
    reference_defaults: WakeFreePkReferenceDefaultsOut


class WakeFreeCortisolModelOut(ApiModel):
    id: Literal["hc-wake-free-v3", "hc-mixed-route-free-v4"]
    revision: Literal[
        "hc-wake-free-v3.0.0",
        "hc-mixed-route-free-v4.0.0",
        "hc-mixed-route-free-v4.1.0",
    ]
    supported_medication: str
    supported_formulation: str
    supported_route: Route
    supported_medications: list[str] | None = None
    supported_formulations: list[str] | None = None
    supported_routes: list[Route] | None = None
    amount_unit: DoseUnit
    binding_revision: Literal["one-site-cbg-linear-albumin-v1"]
    calibration_revision: str
    parameters: WakeFreePkParametersOut
    reference_absorption_duration_hours: Decimal
    reference_clearance_liters_per_hour: Decimal
    free_peak_10_mg_nmol_l: Decimal
    iv_push_supported_amount_mg: Decimal | None = None
    iv_push_supported_amounts_mg: list[Decimal] | None = None
    iv_push_scaling: str | None = None
    iv_push_initial_total_cortisol_nmol_l: Decimal | None = None
    iv_push_elimination_rate_per_hour: Decimal | None = None
    iv_push_elimination_half_life_hours: Decimal | None = None
    contribution_horizon_hours: int = Field(gt=0)
    sample_interval_minutes: int = Field(gt=0)
    references: list[str]

    @field_serializer(
        "reference_absorption_duration_hours",
        "reference_clearance_liters_per_hour",
        "free_peak_10_mg_nmol_l",
        "iv_push_supported_amount_mg",
        "iv_push_initial_total_cortisol_nmol_l",
        "iv_push_elimination_rate_per_hour",
        "iv_push_elimination_half_life_hours",
    )
    def _parameters(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @field_serializer("iv_push_supported_amounts_mg")
    def _amounts(self, value: list[Decimal] | None) -> list[str] | None:
        return None if value is None else [str(amount) for amount in value]


class WakeFreeCortisolSampleOut(ApiModel):
    occurred_at: datetime
    local_time: datetime
    utc_offset_minutes: int
    modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    regular_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    stress_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    derived_total_cortisol_nmol_l_display: Decimal = Field(ge=0)

    @field_serializer(
        "modeled_free_cortisol_nmol_l",
        "regular_modeled_free_cortisol_nmol_l",
        "stress_modeled_free_cortisol_nmol_l",
        "derived_total_cortisol_nmol_l_display",
    )
    def _values(self, value: Decimal) -> str:
        return str(value)


class WakeReferenceCitationOut(ApiModel):
    citation: str
    pmid: str | None = None
    url: str | None = None


class WakeReferenceIdentityOut(ApiModel):
    id: Literal["hc-wake-reference-v1"]
    revision: Literal["hc-wake-reference-v1.0.0"]
    binding_revision: Literal["one-site-cbg-linear-albumin-v1"]
    source_module: str | None = None
    sample_interval_minutes: int | None = Field(default=None, gt=0)
    percentiles: list[Literal["p5", "p25", "p50", "p75", "p95"]] = Field(default_factory=list)
    default_band: list[Literal["p5", "p95"]] = Field(default_factory=list)
    references: list[WakeReferenceCitationOut] = Field(default_factory=list)


class WakeReferenceAssumptionsOut(ApiModel):
    healthy_adult_population_context_only: Literal[True]
    wake_at: datetime
    sleep_onset_at: datetime
    age_years: Decimal
    sex: str
    wake_amplitude_association_applied: bool
    observed_meals: dict[str, datetime]
    unobserved_meals_invented: Literal[False]
    pre_wake_gap_expected: Literal[True]

    @field_serializer("age_years")
    def _age(self, value: Decimal) -> str:
        return str(value)


class WakeReferenceSampleOut(ApiModel):
    occurred_at: datetime
    local_time: datetime
    utc_offset_minutes: int
    hour_local: Decimal
    hours_since_wake: Decimal
    sigma_log: Decimal
    serum_free_p5_nmol_l: Decimal = Field(ge=0)
    serum_free_p25_nmol_l: Decimal = Field(ge=0)
    serum_free_p50_nmol_l: Decimal = Field(ge=0)
    serum_free_p75_nmol_l: Decimal = Field(ge=0)
    serum_free_p95_nmol_l: Decimal = Field(ge=0)
    serum_total_p5_nmol_l: Decimal = Field(ge=0)
    serum_total_p25_nmol_l: Decimal = Field(ge=0)
    serum_total_p50_nmol_l: Decimal = Field(ge=0)
    serum_total_p75_nmol_l: Decimal = Field(ge=0)
    serum_total_p95_nmol_l: Decimal = Field(ge=0)

    @field_serializer(
        "hour_local",
        "hours_since_wake",
        "sigma_log",
        "serum_free_p5_nmol_l",
        "serum_free_p25_nmol_l",
        "serum_free_p50_nmol_l",
        "serum_free_p75_nmol_l",
        "serum_free_p95_nmol_l",
        "serum_total_p5_nmol_l",
        "serum_total_p25_nmol_l",
        "serum_total_p50_nmol_l",
        "serum_total_p75_nmol_l",
        "serum_total_p95_nmol_l",
    )
    def _values(self, value: Decimal) -> str:
        return str(value)


class WakeReferenceOut(ApiModel):
    available: bool
    date: date
    timezone: str
    day_start: datetime | None = None
    day_end: datetime | None = None
    elapsed_hours: Decimal | None = None
    series_kind: Literal["wake_anchored_cortisol_reference"]
    series_unit: Literal["nmol/L"]
    reference: WakeReferenceIdentityOut
    assumptions: WakeReferenceAssumptionsOut | None = None
    missing_inputs: list[Literal["wake_at", "sleep_onset_at"]]
    safety_label: str
    samples: list[WakeReferenceSampleOut]

    @field_serializer("elapsed_hours")
    def _elapsed(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class WakeCoverageAucOut(ApiModel):
    modeled_free_nmol_l_hours: Decimal
    regular_modeled_free_nmol_l_hours: Decimal
    stress_modeled_free_nmol_l_hours: Decimal
    reference_p50_nmol_l_hours: Decimal
    modeled_minus_reference_p50_nmol_l_hours: Decimal
    modeled_to_reference_p50_ratio: Decimal | None

    @field_serializer("*")
    def _values(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class WakeCoverageTroughOut(ApiModel):
    previous_dose_event_id: uuid.UUID
    next_dose_event_id: uuid.UUID
    occurred_at: datetime
    modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    regular_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    stress_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    reference_p5_nmol_l: Decimal = Field(ge=0)
    reference_p25_nmol_l: Decimal = Field(ge=0)
    reference_p50_nmol_l: Decimal = Field(ge=0)
    depth_below_p50_nmol_l: Decimal = Field(ge=0)

    @field_serializer(
        "modeled_free_cortisol_nmol_l",
        "regular_modeled_free_cortisol_nmol_l",
        "stress_modeled_free_cortisol_nmol_l",
        "reference_p5_nmol_l",
        "reference_p25_nmol_l",
        "reference_p50_nmol_l",
        "depth_below_p50_nmol_l",
    )
    def _values(self, value: Decimal) -> str:
        return str(value)


class WakeCoverageMaximumFallOut(ApiModel):
    magnitude_nmol_l_per_hour: Decimal = Field(ge=0)
    interval_started_at: datetime
    interval_ended_at: datetime
    from_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    to_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)

    @field_serializer(
        "magnitude_nmol_l_per_hour",
        "from_modeled_free_cortisol_nmol_l",
        "to_modeled_free_cortisol_nmol_l",
    )
    def _values(self, value: Decimal) -> str:
        return str(value)


class WakeCoverageOvershootOut(ApiModel):
    duration_minutes: Decimal = Field(ge=0)
    maximum_nmol_l: Decimal = Field(ge=0)
    maximum_at: datetime | None

    @field_serializer("duration_minutes", "maximum_nmol_l")
    def _values(self, value: Decimal) -> str:
        return str(value)


class WakeCoverageSymptomContextOut(ApiModel):
    symptom_event_id: uuid.UUID
    occurred_at: datetime
    name: str
    severity: int | None = Field(default=None, ge=0, le=10)
    tracking_category: SymptomTrackingCategory | None
    tracking_category_revision: str | None
    previous_supported_dose_event_ids: list[uuid.UUID]
    previous_dose_categories: list[str]
    minutes_since_previous_supported_dose: Decimal | None = Field(default=None, ge=0)
    modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    regular_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    stress_modeled_free_cortisol_nmol_l: Decimal = Field(ge=0)
    reference_p5_nmol_l: Decimal = Field(ge=0)
    reference_p50_nmol_l: Decimal = Field(ge=0)
    reference_p95_nmol_l: Decimal = Field(ge=0)

    @field_serializer(
        "minutes_since_previous_supported_dose",
        "modeled_free_cortisol_nmol_l",
        "regular_modeled_free_cortisol_nmol_l",
        "stress_modeled_free_cortisol_nmol_l",
        "reference_p5_nmol_l",
        "reference_p50_nmol_l",
        "reference_p95_nmol_l",
    )
    def _values(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class WakeCoverageFeaturesOut(ApiModel):
    available: bool
    feature_id: Literal["hc-wake-coverage-v1"]
    feature_revision: Literal["hc-wake-coverage-v1.1.0"]
    date: date
    timezone: str
    analyzed_from: datetime
    analyzed_through: datetime
    elapsed_hours: Decimal = Field(ge=0)
    day_state: Literal["complete", "partial"]
    safety_label: str
    definitions: dict[str, str]
    missing_inputs: list[str]
    uncategorized_symptom_count: int = Field(ge=0)
    source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_minutes: Decimal | None = Field(default=None, ge=0)
    expected_pre_wake_excluded_minutes: Decimal | None = Field(default=None, ge=0)
    time_below_p5_minutes: Decimal | None = Field(default=None, ge=0)
    time_below_p25_minutes: Decimal | None = Field(default=None, ge=0)
    auc: WakeCoverageAucOut | None
    inter_dose_troughs: list[WakeCoverageTroughOut]
    maximum_fall: WakeCoverageMaximumFallOut | None
    p95_overshoot: WakeCoverageOvershootOut | None
    symptom_contexts: list[WakeCoverageSymptomContextOut]

    @field_serializer(
        "elapsed_hours",
        "comparison_minutes",
        "expected_pre_wake_excluded_minutes",
        "time_below_p5_minutes",
        "time_below_p25_minutes",
    )
    def _values(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class WakeFreeCortisolCurveOut(ApiModel):
    date: date
    timezone: str
    day_start: datetime
    day_end: datetime
    elapsed_hours: Decimal
    series_kind: Literal["modeled_serum_free_cortisol_scenario"]
    series_name: Literal["Modeled serum-free-cortisol scenario"]
    series_unit: Literal["nmol/L"]
    safety_label: str
    definition: str
    model: WakeFreeCortisolModelOut
    source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dose_markers: list[SteroidExposureDoseMarker]
    samples: list[WakeFreeCortisolSampleOut]
    supported_dose_count: int = Field(ge=0)
    excluded_dose_count: int = Field(ge=0)
    context_band: CircadianContextBandOut
    wake_reference: WakeReferenceOut
    coverage_features: WakeCoverageFeaturesOut

    @field_serializer("elapsed_hours")
    def _elapsed(self, value: Decimal) -> str:
        return str(value)


class DecimalRangeOut(ApiModel):
    minimum: Decimal | None
    average: Decimal | None
    maximum: Decimal | None

    @field_serializer("minimum", "average", "maximum")
    def _values(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class DailyPatternWearableOut(DecimalRangeOut):
    metric_type: GarminMetricType
    unit: str | None
    sample_count: int = Field(ge=0)
    samples_without_cadence: int = Field(ge=0)
    observed_coverage_minutes: Decimal = Field(ge=0)
    observed_coverage_percent: Decimal = Field(ge=0, le=100)
    gap_count: int | None = Field(default=None, ge=0)
    largest_gap_minutes: Decimal | None = Field(default=None, ge=0)
    missingness_state: Literal[
        "no_samples",
        "cadence_unavailable",
        "partial_observed_coverage",
        "full_observed_coverage",
    ]
    incompatible_units: bool
    source_revision_watermark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary_version: Literal["hc-wearable-daily-v1"]

    @field_serializer(
        "observed_coverage_minutes", "observed_coverage_percent", "largest_gap_minutes"
    )
    def _coverage(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class DailyPatternSymptomTimingOut(ApiModel):
    symptom_event_id: uuid.UUID
    occurred_at: datetime
    name: str
    severity: int | None = Field(default=None, ge=0, le=10)
    previous_supported_dose_event_ids: list[uuid.UUID]
    minutes_since_previous_supported_dose: Decimal | None = Field(default=None, ge=0)
    theoretical_exposure_reu: Decimal = Field(ge=0)

    @field_serializer("minutes_since_previous_supported_dose", "theoretical_exposure_reu")
    def _decimal_values(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class DailyPatternBloodPressureOut(ApiModel):
    sample_count: int = Field(ge=0)
    pulse_sample_count: int = Field(ge=0)
    pulse_missing_count: int = Field(ge=0)
    systolic: DecimalRangeOut
    diastolic: DecimalRangeOut
    pulse: DecimalRangeOut


class DailyPatternEpisodesOut(ApiModel):
    count: int = Field(ge=0)
    open_count: int = Field(ge=0)
    overlap_minutes: Decimal = Field(ge=0)

    @field_serializer("overlap_minutes")
    def _duration(self, value: Decimal) -> str:
        return str(value)


class DailyPatternDayOut(ApiModel):
    date: date
    timezone: str
    elapsed_hours: Decimal = Field(gt=0)
    feature_version: Literal["hc-daily-pattern-v1"]
    exposure_model_version: str
    dose_plan_version_ids: list[uuid.UUID]
    source_revision_watermark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    supported_dose_count: int = Field(ge=0)
    excluded_dose_count: int = Field(ge=0)
    exposure_peak_reu: Decimal = Field(ge=0)
    exposure_peak_at: datetime
    exposure_auc_reu_hours: Decimal = Field(ge=0)
    symptom_count: int = Field(ge=0)
    symptom_severity_sample_count: int = Field(ge=0)
    symptom_severity_missing_count: int = Field(ge=0)
    average_symptom_severity: Decimal | None = Field(default=None, ge=0, le=10)
    symptom_timings: list[DailyPatternSymptomTimingOut]
    wearables: list[DailyPatternWearableOut]
    blood_pressure: DailyPatternBloodPressureOut
    stress_episodes: DailyPatternEpisodesOut

    @field_serializer(
        "elapsed_hours",
        "exposure_peak_reu",
        "exposure_auc_reu_hours",
        "average_symptom_severity",
    )
    def _daily_decimals(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class LongitudinalMetricOut(ApiModel):
    key: str
    label: str
    unit: str
    observed_days: int = Field(ge=0)
    missing_days: int = Field(ge=0)
    observed_day_percent: Decimal = Field(ge=0, le=100)
    minimum: Decimal | None
    median: Decimal | None
    maximum: Decimal | None
    first_observed: Decimal | None
    last_observed: Decimal | None
    first_to_last_change: Decimal | None
    trend_eligible: bool

    @field_serializer(
        "observed_day_percent",
        "minimum",
        "median",
        "maximum",
        "first_observed",
        "last_observed",
        "first_to_last_change",
    )
    def _decimal_values(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class ModelVersionPeriodOut(ApiModel):
    date_from: date
    date_to: date
    feature_version: str
    exposure_model_version: str


class LongitudinalSummaryOut(ApiModel):
    total_days: int = Field(ge=0)
    minimum_observed_days_for_trend: int = Field(gt=0)
    coverage_definition: str
    multiple_comparison_caution: str
    metrics: list[LongitudinalMetricOut]
    model_version_periods: list[ModelVersionPeriodOut]


class PatternAnalysisOut(AiResource):
    id: uuid.UUID
    analysis_type: Literal["pattern_observation"]
    body: str
    source_record_ids: list[str]
    computed_inputs: dict[str, object]
    range_start: datetime
    range_end: datetime
    model_digest: str
    schema_version: str


class PatternAnalysisGenerationOut(ApiModel):
    outcome: Literal[
        "created",
        "refused",
        "model_unavailable",
        "model_timeout",
        "model_invalid_response",
        "invalid",
    ]
    detail: str | None = None
    analysis: PatternAnalysisOut | None = None


class DayAnalysisOut(AiResource):
    id: uuid.UUID
    analysis_type: Literal["daily_summary"]
    body: str
    selected_date: date
    timezone: str
    source_revision_sha256: str = Field(min_length=64, max_length=64)
    source_record_count: int = Field(ge=1)
    model_digest: str
    schema_version: str
    stale: bool


class DayAnalysisGenerationOut(ApiModel):
    outcome: Literal[
        "created",
        "refused",
        "model_unavailable",
        "model_timeout",
        "model_invalid_response",
        "invalid",
    ]
    detail: str | None = None
    analysis: DayAnalysisOut | None = None


class DailyPatternsOut(ApiModel):
    date_from: date
    date_to: date
    timezone: str
    feature_version: Literal["hc-daily-pattern-v1"]
    safety_label: str
    definitions: dict[str, str]
    exposure_model_versions: list[str]
    longitudinal_summary: LongitudinalSummaryOut
    days: list[DailyPatternDayOut]
