"""SAFE-01: facts, plans, and AI output occupy separate storage namespaces.

All bases share one ``MetaData`` and one registry, because a dose legitimately
references a medication and an owner across schema boundaries, and neither a foreign
key nor a ``relationship()`` string can resolve across separate registries.

So the partition is guaranteed by two things instead, and both are asserted here:

1. **Which base a model inherits** decides its category (:func:`category_of`).
2. **Every model names its schema**, and that schema must match its category.

The strongest enforcement is not here at all -- it is the PostgreSQL grant that denies
the AI role any write on ``fact`` and ``plan`` (see
``tests/integration/test_schema_privileges.py``).
"""

from __future__ import annotations

import pytest

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import (
    SCHEMAS,
    AIBase,
    Base,
    Category,
    FactBase,
    IdentityBase,
    OpsBase,
    PlanBase,
    category_of,
)
from healthcurve.models import EXPECTED_TABLE_COUNT

#: Every mapped model, with the schema it must live in.
_BASE_TO_SCHEMA = {
    FactBase: "fact",
    PlanBase: "plan",
    AIBase: "ai",
    OpsBase: "ops",
    IdentityBase: "identity",
}


def _mapped_models() -> list[type]:
    return [m.class_ for m in Base.registry.mappers]


@pytest.mark.safety("SAFE-01")
def test_every_model_lands_in_the_schema_its_base_requires() -> None:
    """A model on FactBase must be in `fact`, and so on. No exceptions."""
    wrong: list[str] = []
    for model in _mapped_models():
        expected = next(
            (schema for base, schema in _BASE_TO_SCHEMA.items() if issubclass(model, base)),
            None,
        )
        assert expected is not None, f"{model.__name__} inherits no known base"
        actual = model.__table__.schema
        if actual != expected:
            wrong.append(f"{model.__name__}: in {actual!r}, expected {expected!r}")
    assert not wrong, f"models in the wrong namespace: {wrong}"


@pytest.mark.safety("SAFE-01")
def test_every_model_declares_a_schema() -> None:
    """A model with no schema would land in `public`, outside the partition."""
    unschemed = [m.__name__ for m in _mapped_models() if not m.__table__.schema]
    assert not unschemed, f"models with no schema: {unschemed}"


@pytest.mark.safety("SAFE-01")
def test_category_of_matches_the_table_schema() -> None:
    """The SAFE-02 discriminator and the storage namespace cannot disagree."""
    for model in _mapped_models():
        category = category_of(model)
        if category is None:
            # Operational and identity tables are not one of the three categories.
            assert model.__table__.schema in {"ops", "identity"}
        else:
            assert model.__table__.schema == category.value


@pytest.mark.safety("SAFE-01")
def test_no_authoritative_or_operational_foreign_key_points_into_ai() -> None:
    """Facts and plans must never depend on generated content (SAFE-06).

    An FK from outside `ai` would make deleting an analysis capable of affecting an
    authoritative or operational record. AI-internal ownership cascades are allowed.
    """
    offenders: list[str] = []
    for model in _mapped_models():
        for fk in model.__table__.foreign_keys:
            if model.__table__.schema != "ai" and fk.column.table.schema == "ai":
                offenders.append(f"{model.__table__.fullname} -> {fk.target_fullname}")
    assert not offenders, f"foreign keys into the ai namespace: {offenders}"


@pytest.mark.safety("SAFE-01")
def test_ai_tables_hold_no_foreign_key_into_facts_or_plans() -> None:
    """AI references facts by ID only, so its rows can be deleted freely (SAFE-06)."""
    offenders: list[str] = []
    for model in _mapped_models():
        if model.__table__.schema != "ai":
            continue
        for fk in model.__table__.foreign_keys:
            if fk.column.table.schema in {"fact", "plan"}:
                offenders.append(f"{model.__table__.fullname} -> {fk.target_fullname}")
    assert not offenders, f"ai tables with foreign keys into facts/plans: {offenders}"


def test_all_expected_schemas_are_declared() -> None:
    assert set(SCHEMAS) == {"fact", "plan", "ai", "ops", "identity"}
    assert len(set(SCHEMAS)) == len(SCHEMAS)


def test_category_covers_exactly_the_three_safety_namespaces() -> None:
    assert {c.value for c in Category} == {"fact", "plan", "ai"}


def test_the_aggregator_registers_every_model() -> None:
    """A new model that is not imported in healthcurve.models would vanish from
    migrations, so the count is pinned deliberately."""
    assert len(Base.metadata.tables) == EXPECTED_TABLE_COUNT, (
        f"{len(Base.metadata.tables)} tables registered but EXPECTED_TABLE_COUNT is "
        f"{EXPECTED_TABLE_COUNT}; add the model to healthcurve/models.py and update the count"
    )
