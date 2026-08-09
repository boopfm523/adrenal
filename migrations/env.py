"""Alembic environment.

Two things here are deliberate:

* The database URL comes from application settings, not from ``alembic.ini``, so a
  migration can never be pointed at a different database than the application uses
  and so no credential lives in a committed file (threat model T3).
* ``include_schemas`` plus an explicit schema filter means autogenerate sees the
  ``fact``, ``plan``, ``ai``, and ``ops`` partition and does not try to "clean up"
  tables it does not own.
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing this registers every model on the shared MetaData. Without it,
# autogenerate would see the missing tables as deletions and write a migration
# that drops them.
import healthcurve.models  # noqa: F401
from healthcurve.config import get_settings
from healthcurve.db import SCHEMAS, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# All bases share one MetaData (see healthcurve.db), so this is the whole schema --
# fact, plan, ai, ops, and identity together.
target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Ignore anything outside our four schemas (extensions, PostGIS, etc.)."""
    if type_ == "table":
        schema = getattr(obj, "schema", None)
        return schema in SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
            # Keep alembic's own bookkeeping out of the safety namespaces.
            version_table_schema="ops",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
