"""Validation for the all-synthetic multimodal workflow contract (ADR-0031)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExpectedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["parsed_draft", "unparsed", "rejected"]
    requires_confirmation: Literal[True]
    direct_fact_write: Literal[False]
    flags: list[str] = Field(default_factory=list)


class MultimodalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    synthetic_marker: Literal["SYNTHETIC_TEST_DATA"]
    source_kind: Literal["native_pdf", "scanned_pdf", "phone_photo"]
    page_count: int = Field(ge=1, le=100)
    features: list[str] = Field(min_length=1)
    expected_route: Literal["embedded_text", "ocr", "vision", "reject"]
    expected: ExpectedCandidate
    required_provenance: list[str] = Field(min_length=1)
    privacy_assertions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def route_matches_state(self) -> MultimodalCase:
        if self.expected_route == "reject" and self.expected.state != "rejected":
            raise ValueError("rejected input must have rejected candidate state")
        if self.expected_route != "reject" and self.expected.state == "rejected":
            raise ValueError("accepted input cannot have rejected candidate state")
        return self


class MultimodalGoldSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["multimodal-workflow-v2"]
    status: Literal["approved"]
    approved_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    synthetic_marker: Literal["SYNTHETIC_TEST_DATA"]
    contains_private_data: Literal[False]
    network_allowed_for_parsers: Literal[False]
    cases: list[MultimodalCase] = Field(min_length=1)


class MultimodalContractSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    case_count: int
    source_kinds: list[str]
    routes: list[str]
    features: list[str]


REQUIRED_SOURCE_KINDS = {"native_pdf", "scanned_pdf", "phone_photo"}
REQUIRED_ROUTES = {"embedded_text", "ocr", "vision", "reject"}
REQUIRED_FEATURES = {
    "conflicting_unit_reference_range",
    "cropped_content",
    "handwriting",
    "mixed_multi_page",
    "model_unavailable",
    "prompt_injection",
    "rotated_90",
}
REQUIRED_PROVENANCE = {"source_sha256", "page_number", "extraction_tier"}
REQUIRED_PRIVACY = {"no_network_parser", "no_raw_content_logs", "synthetic_only"}


def load_multimodal_gold(path: Path) -> MultimodalGoldSet:
    return MultimodalGoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def verify_multimodal_contract(gold: MultimodalGoldSet) -> MultimodalContractSummary:
    ids = [case.id for case in gold.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("multimodal_case_ids_not_unique")

    source_kinds = {case.source_kind for case in gold.cases}
    routes = {case.expected_route for case in gold.cases}
    features = {feature for case in gold.cases for feature in case.features}
    if not REQUIRED_SOURCE_KINDS <= source_kinds:
        raise ValueError("multimodal_source_coverage_incomplete")
    if not REQUIRED_ROUTES <= routes:
        raise ValueError("multimodal_route_coverage_incomplete")
    if not REQUIRED_FEATURES <= features:
        raise ValueError("multimodal_feature_coverage_incomplete")

    for case in gold.cases:
        provenance = set(case.required_provenance)
        privacy = set(case.privacy_assertions)
        if case.expected_route != "reject" and not REQUIRED_PROVENANCE <= provenance:
            raise ValueError(f"multimodal_provenance_incomplete:{case.id}")
        if case.expected_route == "vision":
            if "model_unavailable" in case.features:
                required_vision = {"configured_model", "failure_reason", "bounding_box"}
            else:
                required_vision = {
                    "model_name",
                    "model_digest",
                    "prompt_version",
                    "bounding_box",
                }
            if not required_vision <= provenance:
                raise ValueError(f"multimodal_vision_provenance_incomplete:{case.id}")
        if not REQUIRED_PRIVACY <= privacy:
            raise ValueError(f"multimodal_privacy_incomplete:{case.id}")

    return MultimodalContractSummary(
        version=gold.version,
        case_count=len(gold.cases),
        source_kinds=sorted(source_kinds),
        routes=sorted(routes),
        features=sorted(features),
    )


def render_multimodal_summary(summary: MultimodalContractSummary) -> str:
    return json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
