"""SAFE-01: facts, plans, and AI output occupy separate storage namespaces."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer

from healthcurve.db import (
    SCHEMAS,
    AIBase,
    Category,
    FactBase,
    OpsBase,
    PlanBase,
    category_of,
)


@pytest.mark.safety("SAFE-01")
def test_each_base_maps_to_its_own_schema() -> None:
    assert FactBase.metadata.schema == "fact"
    assert PlanBase.metadata.schema == "plan"
    assert AIBase.metadata.schema == "ai"
    assert OpsBase.metadata.schema == "ops"


@pytest.mark.safety("SAFE-01")
def test_schemas_are_distinct() -> None:
    assert len(set(SCHEMAS)) == len(SCHEMAS)


@pytest.mark.safety("SAFE-01")
def test_category_of_identifies_the_owning_namespace() -> None:
    class Dose(FactBase):
        __tablename__ = "t_dose_probe"
        id = Column(Integer, primary_key=True)

    class Slot(PlanBase):
        __tablename__ = "t_slot_probe"
        id = Column(Integer, primary_key=True)

    class Analysis(AIBase):
        __tablename__ = "t_analysis_probe"
        id = Column(Integer, primary_key=True)

    class Job(OpsBase):
        __tablename__ = "t_job_probe"
        id = Column(Integer, primary_key=True)

    assert category_of(Dose) is Category.FACT
    assert category_of(Slot) is Category.PLAN
    assert category_of(Analysis) is Category.AI
    # Operational tables are not one of the three categories and must not claim one.
    assert category_of(Job) is None


@pytest.mark.safety("SAFE-01")
def test_bases_do_not_share_metadata() -> None:
    """A shared MetaData would let a model land in the wrong schema by omission."""
    registries = {
        id(FactBase.metadata),
        id(PlanBase.metadata),
        id(AIBase.metadata),
        id(OpsBase.metadata),
    }
    assert len(registries) == 4
