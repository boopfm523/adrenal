"""Every enum-typed column must round-trip as its enum, not as a bare string.

This exists because of a bug that got all the way to a commit: a column declared
``Mapped[RegimenStatus]`` but backed by a plain ``String`` reads back as ``str``, so
``version.status is RegimenStatus.APPROVED`` is silently always ``False``. That would
have let an approved plan version be approved a second time, and a resolved episode
read as still open.

Converting the columns by hand missed one (two models had byte-identical lines). So the
rule is checked across every mapped column instead of trusted to a careful edit.
"""

from __future__ import annotations

from enum import StrEnum
from typing import get_args, get_origin, get_type_hints

import pytest

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import Base, StrEnumType


def _enum_from_annotation(annotation: object) -> type[StrEnum] | None:
    """The StrEnum in ``Mapped[X]`` or ``Mapped[X | None]``, if there is one."""
    candidates = [annotation]
    if get_origin(annotation) is not None:
        candidates.extend(get_args(annotation))
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, StrEnum):
            return candidate
    return None


def _enum_columns() -> list[tuple[str, str, type[StrEnum], object]]:
    """(model, column, enum class, column type) for every enum-annotated column."""
    found: list[tuple[str, str, type[StrEnum], object]] = []
    for mapper in Base.registry.mappers:
        model = mapper.class_
        # Deliberately not guarded: a model whose annotations cannot be resolved would
        # be skipped silently, which is exactly the hole this test exists to close.
        hints = get_type_hints(model)

        for column_property in mapper.column_attrs:
            name = column_property.key
            annotation = hints.get(name)
            if annotation is None:
                continue
            # Mapped[X] -> X
            inner = get_args(annotation)[0] if get_args(annotation) else annotation
            enum_class = _enum_from_annotation(inner)
            if enum_class is None:
                continue
            found.append((model.__name__, name, enum_class, mapper.columns[name].type))
    return found


def test_there_are_enum_columns_to_check() -> None:
    """Guard against the discovery above silently finding nothing."""
    assert len(_enum_columns()) >= 15


@pytest.mark.safety("SAFE-14")
def test_every_enum_column_uses_the_enum_adapter() -> None:
    """A plain String here makes every `is` comparison against the enum False."""
    offenders = [
        f"{model}.{column} is {type(column_type).__name__}, expected StrEnumType"
        for model, column, _enum, column_type in _enum_columns()
        if not isinstance(column_type, StrEnumType)
    ]
    assert not offenders, (
        "enum-typed columns backed by a plain type: "
        f"{offenders}. Use StrEnumType(TheEnum, length) so the value round-trips as "
        "the enum -- otherwise `x is SomeEnum.MEMBER` is silently always False."
    )


@pytest.mark.safety("SAFE-14")
def test_each_adapter_carries_the_matching_enum() -> None:
    """A copy-paste that pairs a column with the wrong enum would corrupt reads."""
    mismatched = [
        f"{model}.{column}: annotated {enum_class.__name__}, adapter holds "
        f"{column_type.enum_class.__name__}"
        for model, column, enum_class, column_type in _enum_columns()
        if isinstance(column_type, StrEnumType) and column_type.enum_class is not enum_class
    ]
    assert not mismatched, f"enum adapter does not match the annotation: {mismatched}"


def test_adapter_round_trips_to_the_enum() -> None:
    """The behaviour the whole rule depends on."""
    from healthcurve.medications.models import RegimenStatus

    adapter = StrEnumType(RegimenStatus, 16)
    stored = adapter.process_bind_param(RegimenStatus.APPROVED, None)
    assert stored == "approved"

    loaded = adapter.process_result_value("approved", None)
    assert loaded is RegimenStatus.APPROVED, "identity comparison must hold after a read"
    assert adapter.process_result_value(None, None) is None
