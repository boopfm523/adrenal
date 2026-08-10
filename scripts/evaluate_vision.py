"""Record or verify the real local model's all-synthetic lab-image baseline."""

from __future__ import annotations

import argparse
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from healthcurve.ai.evaluation import EvaluationError
from healthcurve.ai.ollama import OllamaClient
from healthcurve.ai.vision import VISION_PROMPT_VERSION, apply_vision_fallback
from healthcurve.ai.vision_evaluation import (
    VisionCasePrediction,
    VisionEvaluationReport,
    VisionGoldCase,
    VisionPredictionRow,
    load_vision_gold,
    load_vision_report,
    render_vision_report,
    verify_vision_report,
)
from healthcurve.config import Settings
from healthcurve.labs.documents import DocumentLayout, write_page_preview
from healthcurve.labs.pdf_schemas import EmbeddedExtractionResult, PdfDraftCandidate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "evals" / "vision" / "gold-v1.json"
DEFAULT_BASELINE = ROOT / "evals" / "vision" / "baseline-qwen3-vl-30b.json"


def record(gold_path: Path, baseline_path: Path) -> int:
    gold = load_vision_gold(gold_path)
    settings = Settings()
    client = OllamaClient(settings)
    identity = client.identity(settings.ollama_vision_model)
    if identity is None:
        raise EvaluationError("vision_model_identity_unavailable")
    with tempfile.TemporaryDirectory(prefix="healthcurve-vision-gold-") as temporary:
        predictions = [
            _run_case(case, Path(temporary) / case.id, settings=settings, client=client)
            for case in gold.cases
        ]
    report = VisionEvaluationReport(
        gold_set_version=gold.version,
        prompt_version=VISION_PROMPT_VERSION,
        model_name=identity.name,
        model_digest=identity.digest,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )
    summary = verify_vision_report(gold, report)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(render_vision_report(report), encoding="utf-8")
    _print(report, summary.scores, summary.failures)
    return 0 if summary.passed else 1


def check(gold_path: Path, baseline_path: Path) -> int:
    gold = load_vision_gold(gold_path)
    report = load_vision_report(baseline_path)
    summary = verify_vision_report(gold, report)
    _print(report, summary.scores, summary.failures)
    return 0 if summary.passed else 1


def _run_case(
    case: VisionGoldCase, root: Path, *, settings: Settings, client: OllamaClient
) -> VisionCasePrediction:
    layout = DocumentLayout(root)
    layout.prepare()
    document_id = uuid.uuid4()
    source = root / "synthetic-page.png"
    _render_case(case, source)
    write_page_preview(layout, document_id, 1, source)
    lower = PdfDraftCandidate(
        page_number=1,
        row_index=1,
        extraction_tier="ocr",
        coordinate_space="rendered_pixels",
        parsed=False,
        source_text=case.prompt_injection_text or "synthetic unresolved table",
        x0=0,
        top=0,
        x1=1,
        bottom=1,
        confidence=0,
        flags=["unparsed_row", "low_confidence"],
    )
    extraction = EmbeddedExtractionResult(
        document_id=document_id,
        sha256="b" * 64,
        extractor_name="synthetic-gold-renderer",
        extractor_version=case.synthetic_marker,
        extraction_tier="ocr",
        textless_pages=[1],
        ocr_pages=[1],
        vision_pages=[1],
        page_count=1,
        parsed_count=0,
        unparsed_count=1,
        adequate=False,
        candidates=[lower],
    )
    result = apply_vision_fallback(extraction, layout=layout, settings=settings, client=client)
    if result.model_digest is None:
        raise EvaluationError(f"vision_model_run_failed:{case.id}")
    rows = [
        VisionPredictionRow(
            analyte_name=candidate.analyte_name or "",
            original_value=candidate.original_value or "",
            original_unit=candidate.original_unit,
            original_reference_range=candidate.original_reference_range,
            box=(candidate.x0, candidate.top, candidate.x1, candidate.bottom),
            prompt_injection_flagged="prompt_injection_suspected" in candidate.flags,
        )
        for candidate in result.candidates
        if candidate.parsed and candidate.extraction_tier == "vision"
    ]
    return VisionCasePrediction(id=case.id, rows=rows)


def _render_case(case: VisionGoldCase, path: Path) -> None:
    image = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=30)
    body_font = ImageFont.load_default(size=24)
    draw.text((50, 35), case.synthetic_marker, fill="black", font=title_font)
    draw.text((50, 85), "Synthetic laboratory results - page 1", fill="black", font=body_font)
    draw.text((50, 135), "Analyte", fill="black", font=body_font)
    draw.text((500, 135), "Value", fill="black", font=body_font)
    draw.text((690, 135), "Unit", fill="black", font=body_font)
    draw.text((880, 135), "Reference range", fill="black", font=body_font)
    for row in case.rows:
        x0, top, x1, bottom = row.box
        if case.layout == "cards":
            draw.rounded_rectangle((x0, top, x1, bottom), radius=10, outline="gray", width=2)
        else:
            draw.line((x0, bottom, x1, bottom), fill="gray", width=1)
        y = top + 15
        draw.text((x0 + 15, y), row.analyte_name, fill="black", font=body_font)
        draw.text((500, y), row.original_value, fill="black", font=body_font)
        draw.text((690, y), row.original_unit or "", fill="black", font=body_font)
        draw.text((880, y), row.original_reference_range or "", fill="black", font=body_font)
    if case.prompt_injection_text:
        draw.text((50, 620), case.prompt_injection_text, fill="gray", font=body_font)
    image.save(path, format="PNG")


def _print(report: VisionEvaluationReport, scores: dict[str, float], failures: list[str]) -> None:
    print(f"gold={report.gold_set_version} prompt={report.prompt_version}")
    print(f"model={report.model_name} digest={report.model_digest}")
    for field, value in sorted(scores.items()):
        print(f"{field}: {value:.3f}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    try:
        return record(args.gold, args.baseline) if args.record else check(args.gold, args.baseline)
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"vision evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
