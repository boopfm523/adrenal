"""Atomic, qualification-gated selection of the local text model."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Final

from healthcurve.ai.model_qualification import CandidateQualificationReport
from healthcurve.ai.ollama import OllamaClient
from healthcurve.config import Settings

DEFAULT_TEXT_MODEL: Final = "qwen3:30b"
MODEL_ENV_KEY: Final = "HC_OLLAMA_MODEL"


class ModelSelectionError(RuntimeError):
    """A reason-coded refusal to alter local model selection."""


def load_qualification(path: Path) -> CandidateQualificationReport:
    try:
        report = CandidateQualificationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelSelectionError("qualification_report_invalid") from exc
    if not report.passed or not report.suites or not all(suite.passed for suite in report.suites):
        raise ModelSelectionError("candidate_not_qualified")
    return report


def verify_qualified_identity(report: CandidateQualificationReport, settings: Settings) -> None:
    identity = OllamaClient(settings).identity(report.candidate.model_name)
    if identity is None:
        raise ModelSelectionError("qualified_candidate_unavailable")
    if identity.digest != report.candidate.model_digest:
        raise ModelSelectionError("qualified_candidate_digest_changed")


def select_qualified_model(*, qualification_path: Path, env_path: Path, settings: Settings) -> str:
    report = load_qualification(qualification_path)
    verify_qualified_identity(report, settings)
    replace_model_env_selection(env_path, report.candidate.model_name)
    return report.candidate.model_name


def select_default_model(*, env_path: Path, settings: Settings) -> str:
    identity = OllamaClient(settings).identity(DEFAULT_TEXT_MODEL)
    if identity is None:
        raise ModelSelectionError("rollback_model_unavailable")
    replace_model_env_selection(env_path, DEFAULT_TEXT_MODEL)
    return DEFAULT_TEXT_MODEL


def replace_model_env_selection(path: Path, value: str) -> None:
    """Replace one non-secret selection key without exposing the rest of the env file."""
    if not path.is_file():
        raise ModelSelectionError("env_file_missing")
    original = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^[ \t]*(?:export[ \t]+)?{re.escape(MODEL_ENV_KEY)}[ \t]*=", re.MULTILINE
    )
    matches = list(pattern.finditer(original))
    if len(matches) > 1:
        raise ModelSelectionError("duplicate_model_selection")

    lines = original.splitlines(keepends=True)
    replacement = f"{MODEL_ENV_KEY}={value}\n"
    if matches:
        selected_line = original.count("\n", 0, matches[0].start())
        had_newline = lines[selected_line].endswith(("\n", "\r"))
        lines[selected_line] = replacement if had_newline else replacement.rstrip("\n")
        updated = "".join(lines)
    else:
        separator = "" if not original or original.endswith(("\n", "\r")) else "\n"
        updated = f"{original}{separator}{replacement}"

    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    except OSError as exc:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise ModelSelectionError("env_file_update_failed") from exc
