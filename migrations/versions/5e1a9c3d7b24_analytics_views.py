"""curated read-only analytics views and view-only analyst roles (ADR-0036)

Revision ID: 5e1a9c3d7b24
Revises: 9c2e7a4d1b60
Create Date: 2026-09-14

Views expose current fact revisions only, pre-derive local dates and error-prone values,
and keep free text in a separate ``analytics_text`` schema. They are owned by the
NOLOGIN ``healthcurve_analytics_owner`` role, so ``healthcurve_analyst`` and
``healthcurve_analyst_text`` read the views without any base-table privilege. Login
passwords for the analyst roles are set outside migrations by
``deploy/postgres-init/03-analyst-roles.sh``.
"""

from typing import Sequence, Union

from alembic import op

# ruff: noqa: S608 -- view SQL is assembled only from constant schema/table/column
# identifiers defined in this file; no runtime or user input reaches these strings.

revision: str = "5e1a9c3d7b24"
down_revision: Union[str, Sequence[str], None] = "9c2e7a4d1b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OWNER_ROLE = "healthcurve_analytics_owner"
ANALYST_ROLE = "healthcurve_analyst"
ANALYST_TEXT_ROLE = "healthcurve_analyst_text"

BASE_TABLES = (
    "fact.blood_pressure_event",
    "fact.context_event",
    "fact.diary_event",
    "fact.dose_event",
    "fact.emergency_injection_event",
    "fact.garmin_activity_event",
    "fact.garmin_metric_event",
    "fact.garmin_sleep_event",
    "fact.lab_panel",
    "fact.lab_result",
    "fact.life_event",
    "fact.meal_event",
    "fact.stress_episode",
    "fact.symptom_event",
    "fact.temperature_event",
    "fact.weight_event",
    "plan.medication",
    "plan.regimen_dose_slot",
    "plan.regimen_version",
    "ops.wearable_daily_summary",
)


def _current(alias: str, table: str) -> str:
    """A row is current when no later revision supersedes it (linear correction chains)."""
    return (
        f"NOT EXISTS (SELECT 1 FROM fact.{table} superseding "
        f"WHERE superseding.supersedes_id = {alias}.id)"
    )


def _event_time(alias: str) -> str:
    return (
        f"{alias}.occurred_at, {alias}.local_time, "
        f"{alias}.local_time::date AS local_date, {alias}.timezone"
    )


VIEWS: tuple[tuple[str, str], ...] = (
    (
        "analytics.sleep_sessions",
        f"""
        SELECT s.id AS sleep_id,
               s.sleep_kind,
               s.occurred_at AS start_at,
               s.ended_at AS end_at,
               s.timezone,
               s.occurred_at AT TIME ZONE s.timezone AS start_local,
               s.ended_at AT TIME ZONE s.timezone AS end_local,
               (s.occurred_at AT TIME ZONE s.timezone)::date AS start_local_date,
               (s.ended_at AT TIME ZONE s.timezone)::date AS end_local_date,
               round(extract(epoch FROM s.ended_at - s.occurred_at) / 60, 1) AS in_bed_minutes,
               round(s.duration_seconds / 60.0, 1) AS asleep_minutes,
               s.overall_sleep_score AS sleep_score,
               s.awakenings,
               s.confirmation_state
        FROM fact.garmin_sleep_event s
        WHERE {_current("s", "garmin_sleep_event")}
        """,
    ),
    (
        # One row per local wake date: the longest current overnight session ending that
        # date (later end breaks ties), matching wake_reference_inputs.
        "analytics.sleep_nights",
        """
        SELECT DISTINCT ON (ss.end_local_date)
               ss.end_local_date AS wake_date,
               ss.sleep_id,
               ss.timezone,
               ss.start_at AS bedtime_at,
               ss.end_at AS wake_at,
               ss.start_local AS bedtime_local,
               ss.end_local AS wake_local,
               ss.start_local::time AS bedtime_clock,
               ss.end_local::time AS wake_clock,
               round(
                   extract(epoch FROM ss.start_local - ((ss.end_local_date - 1) + time '12:00'))
                   / 60,
                   1
               ) AS bedtime_minutes_after_noon,
               round(extract(epoch FROM ss.end_local - ss.end_local_date::timestamp) / 60, 1)
                   AS wake_minutes_after_midnight,
               ss.in_bed_minutes,
               ss.asleep_minutes,
               ss.sleep_score,
               ss.awakenings,
               count(*) OVER (PARTITION BY ss.end_local_date) AS overnight_sessions_ending_that_date
        FROM analytics.sleep_sessions ss
        WHERE ss.sleep_kind = 'overnight' AND ss.confirmation_state = 'provider_imported'
        ORDER BY ss.end_local_date, ss.end_at - ss.start_at DESC, ss.end_at DESC
        """,
    ),
    (
        "analytics.doses",
        f"""
        SELECT d.id AS dose_id,
               d.occurred_at AS taken_at,
               d.local_time AS taken_local,
               d.local_time::date AS local_date,
               d.local_time::time AS local_clock,
               d.timezone,
               d.medication_id,
               m.name AS medication_name,
               d.amount,
               d.unit,
               d.route,
               d.category,
               d.regimen_version_id,
               d.slot_id,
               slot.timing_mode AS slot_timing_mode,
               slot.scheduled_local_time AS slot_scheduled_clock,
               slot.amount AS slot_planned_amount,
               slot.unit AS slot_planned_unit,
               CASE WHEN slot.timing_mode = 'fixed_time' THEN
                   trunc(offset_minutes.value - 1440 * round(offset_minutes.value / 1440))::integer
               END AS minutes_from_scheduled,
               night.wake_at,
               trunc(extract(epoch FROM d.occurred_at - night.wake_at) / 60)::integer
                   AS minutes_after_wake,
               d.episode_id,
               d.source_type,
               d.confirmation_state
        FROM fact.dose_event d
        JOIN plan.medication m ON m.id = d.medication_id
        LEFT JOIN plan.regimen_dose_slot slot ON slot.id = d.slot_id
        LEFT JOIN LATERAL (
            SELECT extract(
                epoch FROM d.local_time - (d.local_time::date + slot.scheduled_local_time)
            ) / 60 AS value
        ) offset_minutes ON true
        LEFT JOIN analytics.sleep_nights night ON night.wake_date = d.local_time::date
        WHERE NOT d.voided AND {_current("d", "dose_event")}
        """,
    ),
    (
        "analytics.daily_garmin_values",
        f"""
        SELECT g.id AS value_id,
               g.local_time::date AS local_date,
               g.timezone,
               g.metric_type,
               g.garmin_field_name AS source_field,
               g.value,
               g.unit,
               g.aggregation,
               g.period_end_at
        FROM fact.garmin_metric_event g
        WHERE g.aggregation = 'daily_summary' AND {_current("g", "garmin_metric_event")}
        """,
    ),
    (
        "analytics.daily_wearables",
        """
        SELECT v.local_date,
               v.timezone,
               max(v.value) FILTER (WHERE v.metric_type = 'steps') AS steps,
               max(v.value) FILTER (WHERE v.metric_type = 'resting_heart_rate')
                   AS resting_heart_rate_bpm,
               max(v.value) FILTER (WHERE v.metric_type = 'stress') AS average_stress_score,
               max(v.value) FILTER (WHERE v.source_field = 'lastNightAvg') AS nightly_hrv_ms,
               max(v.value) FILTER (WHERE v.source_field = 'avgWakingRespirationValue')
                   AS waking_respiration_avg,
               max(v.value) FILTER (WHERE v.source_field = 'avgSleepRespirationValue')
                   AS sleep_respiration_avg
        FROM analytics.daily_garmin_values v
        GROUP BY v.local_date, v.timezone
        """,
    ),
    (
        "analytics.daily_wearable_summaries",
        """
        SELECT w.local_date,
               w.timezone,
               w.metric_type,
               w.unit,
               w.sample_count,
               w.observed_coverage_minutes,
               w.observed_coverage_percent,
               w.gap_count,
               w.largest_gap_minutes,
               w.missingness_state,
               w.minimum,
               w.average,
               w.maximum,
               w.summary_version
        FROM ops.wearable_daily_summary w
        """,
    ),
    (
        "analytics.wearable_samples",
        f"""
        SELECT g.id AS sample_id,
               g.occurred_at AS sample_at,
               g.local_time AS sample_local,
               g.local_time::date AS local_date,
               g.timezone,
               g.metric_type,
               g.value,
               g.unit,
               g.sample_interval_seconds,
               g.period_end_at,
               g.aggregation,
               g.garmin_field_name AS source_field
        FROM fact.garmin_metric_event g
        -- Garmin Connect stores provider_sample rows; reviewed file imports store point or
        -- interval rows. Daily summaries live in daily_garmin_values instead.
        WHERE g.aggregation IN ('provider_sample', 'point', 'interval')
          AND {_current("g", "garmin_metric_event")}
        """,
    ),
    (
        "analytics.activities",
        f"""
        SELECT a.id AS activity_id,
               a.occurred_at AS start_at,
               a.ended_at AS end_at,
               a.local_time AS start_local,
               a.local_time::date AS local_date,
               a.timezone,
               a.sport,
               a.sub_sport,
               round(a.elapsed_seconds / 60, 1) AS elapsed_minutes,
               a.distance_miles,
               a.calories,
               a.average_heart_rate,
               a.maximum_heart_rate,
               a.environment
        FROM fact.garmin_activity_event a
        WHERE {_current("a", "garmin_activity_event")}
        """,
    ),
    (
        "analytics.symptoms",
        f"""
        SELECT s.id AS symptom_id,
               {_event_time("s")},
               s.name,
               s.severity,
               s.body_area,
               s.tracking_category,
               s.ended_at,
               s.episode_id,
               s.source_type,
               s.confirmation_state
        FROM fact.symptom_event s
        WHERE {_current("s", "symptom_event")}
        """,
    ),
    (
        "analytics.stress_episodes",
        """
        SELECT e.id AS episode_id,
               e.started_at,
               e.ended_at,
               e.timezone,
               e.started_at AT TIME ZONE e.timezone AS started_local,
               e.ended_at AT TIME ZONE e.timezone AS ended_local,
               (e.started_at AT TIME ZONE e.timezone)::date AS start_local_date,
               e.status,
               e.severity,
               e.highest_temperature_c,
               round(extract(epoch FROM e.ended_at - e.started_at) / 60, 1) AS duration_minutes
        FROM fact.stress_episode e
        """,
    ),
    (
        "analytics.emergency_injections",
        f"""
        SELECT i.id AS injection_id,
               {_event_time("i")},
               i.medication_id,
               m.name AS medication_name,
               i.amount,
               i.unit,
               i.route,
               i.emergency_services_called,
               i.transported_to_hospital,
               i.episode_id
        FROM fact.emergency_injection_event i
        JOIN plan.medication m ON m.id = i.medication_id
        WHERE {_current("i", "emergency_injection_event")}
        """,
    ),
    (
        "analytics.blood_pressure",
        f"""
        SELECT b.id AS reading_id,
               {_event_time("b")},
               b.systolic_mmhg,
               b.diastolic_mmhg,
               b.pulse_bpm,
               b.measurement_setting,
               b.body_position,
               b.source_type
        FROM fact.blood_pressure_event b
        WHERE {_current("b", "blood_pressure_event")}
        """,
    ),
    (
        "analytics.temperatures",
        f"""
        SELECT t.id AS reading_id,
               {_event_time("t")},
               t.value,
               t.unit,
               t.normalized_c,
               round(t.normalized_c * 9 / 5 + 32, 1) AS normalized_f
        FROM fact.temperature_event t
        WHERE {_current("t", "temperature_event")}
        """,
    ),
    (
        "analytics.weights",
        f"""
        SELECT w.id AS reading_id,
               {_event_time("w")},
               w.value,
               w.unit,
               w.normalized_kg,
               round(w.normalized_kg * 2.2046226218, 1) AS normalized_lb,
               w.measurement_setting
        FROM fact.weight_event w
        WHERE {_current("w", "weight_event")}
        """,
    ),
    (
        "analytics.meals",
        f"""
        SELECT meal.id AS meal_id,
               {_event_time("meal")},
               meal.size
        FROM fact.meal_event meal
        WHERE {_current("meal", "meal_event")}
        """,
    ),
    (
        "analytics.weather",
        f"""
        SELECT c.id AS observation_id,
               {_event_time("c")},
               c.weather_observed_at,
               c.temperature,
               c.apparent_temperature,
               c.temperature_unit,
               c.pressure,
               c.pressure_unit,
               c.humidity_percent,
               c.precipitation,
               c.precipitation_unit,
               c.conditions,
               c.wind_speed_kph,
               c.wind_gust_kph,
               c.weather_confidence
        FROM fact.context_event c
        WHERE (c.weather_observed_at IS NOT NULL OR c.temperature IS NOT NULL)
          AND {_current("c", "context_event")}
        """,
    ),
    (
        "analytics.lab_results",
        f"""
        SELECT r.id AS result_id,
               r.panel_id,
               p.occurred_at AS collected_at,
               p.local_time AS collected_local,
               p.local_time::date AS local_date,
               p.timezone,
               p.reported_at,
               p.specimen_type,
               p.report_status,
               r.analyte_name,
               r.normalized_analyte_code,
               r.original_value,
               r.qualitative_result,
               r.original_unit,
               r.original_reference_range,
               r.abnormal_flag,
               r.normalized_value,
               r.normalized_unit
        FROM fact.lab_result r
        JOIN fact.lab_panel p ON p.id = r.panel_id
        WHERE {_current("p", "lab_panel")}
        """,
    ),
    (
        "analytics.medications",
        """
        SELECT m.id AS medication_id,
               m.name,
               m.formulation,
               m.strength,
               m.strength_unit,
               m.default_unit,
               m.default_route,
               m.active_from,
               m.active_to
        FROM plan.medication m
        """,
    ),
    (
        "analytics.plan_versions",
        """
        SELECT v.id AS regimen_version_id,
               v.version_label,
               v.status,
               v.effective_timezone,
               v.effective_from_local,
               v.effective_to_local,
               v.effective_from AS effective_from_utc,
               v.effective_to AS effective_to_utc,
               v.approved_at,
               v.retired_at
        FROM plan.regimen_version v
        """,
    ),
    (
        "analytics.plan_dose_slots",
        """
        SELECT s.id AS slot_id,
               s.regimen_version_id,
               v.version_label,
               v.status AS version_status,
               s.medication_id,
               m.name AS medication_name,
               s.timing_mode,
               s.scheduled_local_time AS scheduled_clock,
               s.reminder_local_time AS reminder_clock,
               s.amount,
               s.unit,
               s.route,
               s.sort_order
        FROM plan.regimen_dose_slot s
        JOIN plan.regimen_version v ON v.id = s.regimen_version_id
        JOIN plan.medication m ON m.id = s.medication_id
        """,
    ),
    (
        "analytics_text.diary_entries",
        f"""
        SELECT d.id AS entry_id,
               {_event_time("d")},
               d.text,
               d.tags,
               d.is_sensitive
        FROM fact.diary_event d
        WHERE {_current("d", "diary_event")}
        """,
    ),
    (
        "analytics_text.life_events",
        f"""
        SELECT e.id AS life_event_id,
               {_event_time("e")},
               e.ended_at,
               e.category,
               e.title,
               e.description,
               e.is_sensitive
        FROM fact.life_event e
        WHERE {_current("e", "life_event")}
        """,
    ),
    (
        "analytics_text.plan_slot_conditions",
        """
        SELECT s.id AS slot_id,
               s.regimen_version_id,
               s.condition
        FROM plan.regimen_dose_slot s
        WHERE s.condition IS NOT NULL
        """,
    ),
)

# (record_type, table, alias, extra predicate, free-text fields)
_NOTE_SOURCES: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("symptom", "symptom_event", "x", "", ("notes",)),
    ("dose", "dose_event", "x", "NOT x.voided AND ", ("notes",)),
    ("meal", "meal_event", "x", "", ("notes",)),
    ("blood_pressure", "blood_pressure_event", "x", "", ("notes",)),
    ("temperature", "temperature_event", "x", "", ("notes",)),
    ("weight", "weight_event", "x", "", ("notes",)),
    (
        "emergency_injection",
        "emergency_injection_event",
        "x",
        "",
        ("notes", "reason", "response", "injection_site"),
    ),
    ("activity", "garmin_activity_event", "x", "", ("title", "source_notes")),
    ("lab_panel", "lab_panel", "x", "", ("notes",)),
)


def _record_notes_sql() -> str:
    parts: list[str] = []
    for record_type, table, alias, predicate, fields in _NOTE_SOURCES:
        for field in fields:
            parts.append(
                f"SELECT '{record_type}'::text AS record_type, {alias}.id AS record_id, "
                f"{alias}.occurred_at, {alias}.local_time, {alias}.timezone, "
                f"'{field}'::text AS field, {alias}.{field}::text AS text "
                f"FROM fact.{table} {alias} "
                f"WHERE {predicate}{alias}.{field} IS NOT NULL AND {_current(alias, table)}"
            )
    for field in ("trigger", "illness_description", "recovery_notes", "outcome", "notes"):
        parts.append(
            "SELECT 'stress_episode'::text AS record_type, e.id AS record_id, "
            "e.started_at AS occurred_at, e.started_at AT TIME ZONE e.timezone AS local_time, "
            f"e.timezone, '{field}'::text AS field, e.{field}::text AS text "
            f"FROM fact.stress_episode e WHERE e.{field} IS NOT NULL"
        )
    return "\nUNION ALL\n".join(parts)


def upgrade() -> None:
    op.create_index(
        "ix_garmin_sleep_event_ended_at", "garmin_sleep_event", ["ended_at"], schema="fact"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{OWNER_ROLE}') THEN
                CREATE ROLE {OWNER_ROLE} NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ANALYST_ROLE}') THEN
                CREATE ROLE {ANALYST_ROLE} NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ANALYST_TEXT_ROLE}') THEN
                CREATE ROLE {ANALYST_TEXT_ROLE} NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    for role in (ANALYST_ROLE, ANALYST_TEXT_ROLE):
        # Defense in depth beneath the query validator and per-call transaction settings.
        op.execute(f"ALTER ROLE {role} SET default_transaction_read_only = on")
        op.execute(f"ALTER ROLE {role} SET statement_timeout = '15s'")
        op.execute(f"ALTER ROLE {role} SET idle_in_transaction_session_timeout = '60s'")

    op.execute("CREATE SCHEMA analytics")
    op.execute("CREATE SCHEMA analytics_text")
    op.execute("REVOKE ALL ON SCHEMA analytics, analytics_text FROM PUBLIC")
    op.execute("CREATE VIEW analytics_text.record_notes AS " + _record_notes_sql())
    for name, body in VIEWS:
        op.execute(f"CREATE VIEW {name} AS {body}")

    op.execute(
        f"""
        DO $$
        DECLARE
            view_row record;
        BEGIN
            FOR view_row IN
                SELECT schemaname, viewname FROM pg_views
                WHERE schemaname IN ('analytics', 'analytics_text')
            LOOP
                EXECUTE format(
                    'ALTER VIEW %I.%I OWNER TO {OWNER_ROLE}',
                    view_row.schemaname,
                    view_row.viewname
                );
            END LOOP;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA fact, plan, ops, analytics, analytics_text TO {OWNER_ROLE}")
    op.execute(f"GRANT SELECT ON {', '.join(BASE_TABLES)} TO {OWNER_ROLE}")

    op.execute(f"GRANT USAGE ON SCHEMA analytics TO {ANALYST_ROLE}, {ANALYST_TEXT_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA analytics_text TO {ANALYST_TEXT_ROLE}")
    op.execute(
        f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {ANALYST_ROLE}, {ANALYST_TEXT_ROLE}"
    )
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics_text TO {ANALYST_TEXT_ROLE}")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT USAGE ON SCHEMA analytics, analytics_text TO healthcurve_backup;
                GRANT SELECT ON ALL TABLES IN SCHEMA analytics, analytics_text
                    TO healthcurve_backup;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS analytics_text CASCADE")
    op.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{OWNER_ROLE}') THEN
                REVOKE SELECT ON {", ".join(BASE_TABLES)} FROM {OWNER_ROLE};
                REVOKE USAGE ON SCHEMA fact, plan, ops FROM {OWNER_ROLE};
            END IF;
        END
        $$;
        """
    )
    # The cluster-level roles are retained: they may hold operator-set login passwords.
    op.drop_index("ix_garmin_sleep_event_ended_at", table_name="garmin_sleep_event", schema="fact")
