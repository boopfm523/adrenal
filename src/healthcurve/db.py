"""Database engine, session, and the fact / plan / ai schema partition.

ADR-0001 puts the three safety categories in three PostgreSQL schemas. That partition
is what makes SAFE-01 structural and SAFE-15 / SAFE-16 enforceable as privileges: the
AI worker's database role holds no INSERT or UPDATE on ``fact`` or ``plan``, so
"AI cannot write facts" survives any application bug.

Declarative bases are separate per category on purpose. There is no shared base that
would let a model land in the wrong schema by omission -- picking a base *is* picking
a category, and :func:`category_of` can then answer SAFE-02 for any model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Category(StrEnum):
    """The three categories of docs/safety-spec.md section 1."""

    FACT = "fact"
    PLAN = "plan"
    AI = "ai"


#: Shared naming convention so constraint names are stable across Alembic revisions.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class FactBase(DeclarativeBase):
    """Recorded facts: what the user reported, entered, or imported."""

    metadata = MetaData(schema=Category.FACT.value, naming_convention=NAMING_CONVENTION)


class PlanBase(DeclarativeBase):
    """Physician-approved plan. Never writable by AI (SAFE-16)."""

    metadata = MetaData(schema=Category.PLAN.value, naming_convention=NAMING_CONVENTION)


class AIBase(DeclarativeBase):
    """AI drafts and analyses. Deletable without touching facts or plans (SAFE-06)."""

    metadata = MetaData(schema=Category.AI.value, naming_convention=NAMING_CONVENTION)


#: Operational tables (jobs, audit, import batches) are not one of the three
#: safety categories; they get their own schema so they never blur the partition.
class OpsBase(DeclarativeBase):
    metadata = MetaData(schema="ops", naming_convention=NAMING_CONVENTION)


_BASE_TO_CATEGORY: Final[dict[type[Any], Category]] = {
    FactBase: Category.FACT,
    PlanBase: Category.PLAN,
    AIBase: Category.AI,
}

SCHEMAS: Final[tuple[str, ...]] = (
    Category.FACT.value,
    Category.PLAN.value,
    Category.AI.value,
    "ops",
)


def category_of(model: type[Any]) -> Category | None:
    """The safety category a mapped model belongs to, or None for operational tables.

    Used to populate the SAFE-02 discriminator without each model restating it.
    """
    for base, category in _BASE_TO_CATEGORY.items():
        if issubclass(model, base):
            return category
    return None
