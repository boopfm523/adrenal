"""Imports every mapped model, so the shared MetaData is always complete.

All bases share one MetaData (see :mod:`healthcurve.db`), and a table only exists in it
once its module has been imported. A half-imported metadata is dangerous in two
specific ways:

* ``create_all`` silently builds an incomplete schema, and a foreign key to a missing
  table fails at DDL time with a confusing error.
* Alembic autogenerate sees the missing tables as *deleted* and cheerfully writes a
  migration that drops them.

Importing this module is therefore the supported way to guarantee the metadata is
whole. Migrations and tests import it rather than each maintaining their own list.
"""

from __future__ import annotations

from healthcurve.ai.models import AIAnalysis, ExtractionDraft
from healthcurve.db import Base
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.identity.models import AuthSession, Owner
from healthcurve.integrations.credentials import IntegrationCredential
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminImportBatch,
    GarminMetricEvent,
    GarminSleepEvent,
)
from healthcurve.integrations.telegram.models import TelegramUpdate
from healthcurve.labs.models import LabDocument, LabPanel, LabResult
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseEvent,
    Medication,
    RegimenDoseSlot,
    RegimenVersion,
)
from healthcurve.operations.audit import AuditEntry
from healthcurve.operations.jobs import Job
from healthcurve.reports.models import ReportArtifact, ReportSnapshot

__all__ = [
    "AIAnalysis",
    "ApprovedInstruction",
    "AuditEntry",
    "AuthSession",
    "Base",
    "DiaryEvent",
    "DoseEvent",
    "EmergencyInjectionEvent",
    "ExtractionDraft",
    "GarminActivityEvent",
    "GarminImportBatch",
    "GarminMetricEvent",
    "GarminSleepEvent",
    "IntegrationCredential",
    "Job",
    "LabDocument",
    "LabPanel",
    "LabResult",
    "LifeEvent",
    "Medication",
    "Owner",
    "RegimenDoseSlot",
    "RegimenVersion",
    "ReportArtifact",
    "ReportSnapshot",
    "StressEpisode",
    "SymptomEvent",
    "TelegramUpdate",
]

#: Every table the application owns. Asserted in tests so a new model that is not
#: imported here fails the build rather than silently vanishing from migrations.
EXPECTED_TABLE_COUNT = 27
