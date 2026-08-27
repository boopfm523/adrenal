import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from healthcurve.ai.multimodal_evaluation import (
    REQUIRED_FEATURES,
    load_multimodal_gold,
    verify_multimodal_contract,
)

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "evals" / "vision" / "workflow-gold-v2.json"


def test_approved_multimodal_contract_covers_redesign_and_safety_boundaries() -> None:
    gold = load_multimodal_gold(GOLD_PATH)

    summary = verify_multimodal_contract(gold)

    assert summary.case_count >= 10
    assert set(summary.source_kinds) == {"native_pdf", "phone_photo", "scanned_pdf"}
    assert set(summary.routes) == {"embedded_text", "ocr", "reject", "vision"}
    assert REQUIRED_FEATURES <= set(summary.features)
    assert all(case.expected.requires_confirmation for case in gold.cases)
    assert not any(case.expected.direct_fact_write for case in gold.cases)
    assert gold.contains_private_data is False
    assert gold.network_allowed_for_parsers is False


def test_multimodal_contract_rejects_missing_vision_provenance(tmp_path: Path) -> None:
    payload = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    vision_case = next(case for case in payload["cases"] if case["expected_route"] == "vision")
    vision_case["required_provenance"].remove("model_digest")
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="multimodal_vision_provenance_incomplete"):
        verify_multimodal_contract(load_multimodal_gold(path))


def test_multimodal_contract_cannot_enable_direct_fact_writes(tmp_path: Path) -> None:
    payload = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["expected"]["direct_fact_write"] = True
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_multimodal_gold(path)
