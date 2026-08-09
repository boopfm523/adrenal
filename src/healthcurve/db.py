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

from collections.abc import Iterator
from enum import StrEnum
from functools import lru_cache
from typing import Any, Final

from sqlalchemy import Engine, MetaData, String, TypeDecorator, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from healthcurve.config import Settings, get_settings
from healthcurve.logging import get_logger

log = get_logger(__name__)


class StrEnumType(TypeDecorator[Any]):
    """Store a StrEnum as text and read it back as the enum.

    Without this, a column typed ``Mapped[RegimenStatus]`` but backed by ``String``
    round-trips to a plain ``str``. Every ``status is RegimenStatus.APPROVED`` check
    then silently evaluates False -- which would have let an approved plan version be
    approved a second time, and a resolved episode read as unresolved.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[StrEnum], length: int = 32) -> None:
        super().__init__(length)
        #: Public: the column's enum is part of this adapter's contract.
        self.enum_class = enum_class

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self.enum_class(value)


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


#: One MetaData for every base. Separate MetaData objects cannot resolve a foreign key
#: across schemas, and facts legitimately reference identity.owner and plan.medication.
#: The safety partition is preserved by each model naming its schema explicitly (see
#: the SCHEMA_* dicts below) and by which base it inherits -- not by metadata isolation.
_METADATA = MetaData(naming_convention=NAMING_CONVENTION)

#: Append to a model's __table_args__ to place it in the right namespace.
FACT_SCHEMA: Final[dict[str, str]] = {"schema": "fact"}
PLAN_SCHEMA: Final[dict[str, str]] = {"schema": "plan"}
AI_SCHEMA: Final[dict[str, str]] = {"schema": "ai"}
OPS_SCHEMA: Final[dict[str, str]] = {"schema": "ops"}
IDENTITY_SCHEMA: Final[dict[str, str]] = {"schema": "identity"}


class Base(DeclarativeBase):
    """Root of every model.

    A single root gives one MetaData *and* one registry. The registry matters as much
    as the metadata: a relationship() names its target as a string, and that lookup
    only works within one registry -- a dose legitimately relates to a medication
    across the fact/plan boundary.

    The safety partition is preserved by which base a model inherits (which
    :func:`category_of` reads) and by the schema each model names, not by keeping the
    ORM machinery apart.
    """

    metadata = _METADATA


class FactBase(Base):
    """Recorded facts: what the user reported, entered, or imported."""

    __abstract__ = True


class PlanBase(Base):
    """Physician-approved plan. Never writable by AI (SAFE-16).

    The medication catalogue lives here too. It is not itself a physician approval, but
    it is the vocabulary the plan is written in: letting AI invent a medication would
    let it invent a dose by the back door.
    """

    __abstract__ = True


class AIBase(Base):
    """AI drafts and analyses. Deletable without touching facts or plans (SAFE-06)."""

    __abstract__ = True


class OpsBase(Base):
    """Operational tables (jobs, audit, import batches) -- not a safety category."""

    __abstract__ = True


class IdentityBase(Base):
    """Owner account and sessions. Separate so credentials never sit beside health data."""

    __abstract__ = True


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
    "identity",
)


def category_of(model: type[Any]) -> Category | None:
    """The safety category a mapped model belongs to, or None for operational tables.

    Used to populate the SAFE-02 discriminator without each model restating it.
    """
    for base, category in _BASE_TO_CATEGORY.items():
        if issubclass(model, base):
            return category
    return None


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


def build_engine(url: str) -> Engine:
    """Construct an engine. Uncached, so callers can build one per role.

    Kept separate from the cached accessors below because ``Settings`` is unhashable:
    an ``lru_cache`` that accepted one would raise ``TypeError`` for every caller that
    actually passed it.
    """
    return create_engine(
        url,
        pool_pre_ping=True,
        # Health data must never reach the logs, and echo would print every bound
        # parameter (docs/threat-model.md C2).
        echo=False,
    )


def build_ai_engine(settings: Settings) -> Engine:
    """Engine for the restricted role. See :func:`get_ai_engine`."""
    if not settings.ai_database_url:
        # Refused outright in production by Settings; here it keeps a bare checkout
        # runnable, but it must never pass silently.
        log.warning(
            "ai database url not set; extraction shares the privileged connection",
            reason_code="ai_role_not_separated",
            outcome="degraded",
        )
        return build_engine(settings.database_url)
    return build_engine(settings.ai_database_url)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine(get_settings().database_url)


@lru_cache(maxsize=1)
def get_ai_engine() -> Engine:
    """Engine bound to the restricted role (SAFE-15, SAFE-16).

    Used only where model output becomes a draft. The separation is a database
    privilege rather than a code convention: the connection the model's output flows
    through is denied INSERT, UPDATE and DELETE on ``fact`` and ``plan``, so a prompt
    injection that reaches the query layer still cannot alter the record.

    An earlier design applied the restriction per *process* -- the whole worker ran as
    the AI role. That was the wrong boundary: the worker also performs the owner's
    confirmation, which is a privileged write, so the restriction blocked the human it
    was meant to protect.
    """
    return build_ai_engine(get_settings())


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache(maxsize=1)
def get_ai_session_factory() -> sessionmaker[Session]:
    """Sessions for the extraction path. See :func:`get_ai_engine`."""
    return sessionmaker(get_ai_engine(), expire_on_commit=False)


def session_scope() -> Iterator[Session]:
    """FastAPI dependency: one transaction per request, rolled back on any error."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
