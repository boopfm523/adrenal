from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from healthcurve.ai import model_selection
from healthcurve.ai.model_qualification import (
    CandidatePreflightReport,
    CandidateQualificationReport,
    CandidateSuiteResult,
)
from healthcurve.ai.model_selection import (
    ModelSelectionError,
    load_qualification,
    replace_model_env_selection,
    select_default_model,
    select_qualified_model,
)
from healthcurve.ai.ollama import ModelIdentity
from healthcurve.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _report(*, passed: bool = True) -> CandidateQualificationReport:
    return CandidateQualificationReport(
        generated_at=datetime.now(UTC),
        candidate=CandidatePreflightReport(
            generated_at=datetime.now(UTC),
            base_url="http://127.0.0.1:11434",
            ollama_version="0.33.0",
            minimum_ollama_version="0.33.0",
            model_name="qwen3.8:27b-q8_0",
            model_digest="a" * 64,
            thinking_enabled=False,
            context_window=24_576,
            structured_output=True,
            latency_ms=10,
        ),
        suites=[
            CandidateSuiteResult(
                name="synthetic",
                passed=passed,
                duration_ms=20,
                report_path="synthetic.json",
            )
        ],
        passed=passed,
    )


def test_env_selection_is_atomic_and_preserves_other_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SECRET=preserve-me\nHC_OLLAMA_MODEL=qwen3:30b\n", encoding="utf-8")
    env.chmod(0o600)

    replace_model_env_selection(env, "qwen3.8:27b-q8_0")

    assert env.read_text(encoding="utf-8") == (
        "SECRET=preserve-me\nHC_OLLAMA_MODEL=qwen3.8:27b-q8_0\n"
    )
    assert env.stat().st_mode & 0o777 == 0o600


def test_env_selection_appends_missing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PRESERVED=true", encoding="utf-8")
    replace_model_env_selection(env, "qwen3:30b")
    assert env.read_text(encoding="utf-8") == ("PRESERVED=true\nHC_OLLAMA_MODEL=qwen3:30b\n")


def test_env_selection_refuses_duplicate_or_missing_file(tmp_path: Path) -> None:
    duplicate = tmp_path / ".env"
    duplicate.write_text("HC_OLLAMA_MODEL=a\nHC_OLLAMA_MODEL=b\n", encoding="utf-8")
    with pytest.raises(ModelSelectionError, match="duplicate_model_selection"):
        replace_model_env_selection(duplicate, "candidate")
    with pytest.raises(ModelSelectionError, match="env_file_missing"):
        replace_model_env_selection(tmp_path / "absent", "candidate")


def test_only_passing_qualification_can_be_loaded(tmp_path: Path) -> None:
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(_report().model_dump(mode="json")), encoding="utf-8")
    assert load_qualification(path).candidate.model_name == "qwen3.8:27b-q8_0"

    path.write_text(json.dumps(_report(passed=False).model_dump(mode="json")), encoding="utf-8")
    with pytest.raises(ModelSelectionError, match="candidate_not_qualified"):
        load_qualification(path)


def test_qualified_selection_requires_exact_installed_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        def identity(self, model_name: str) -> ModelIdentity:
            return ModelIdentity(name=model_name, digest="a" * 64)

    monkeypatch.setattr(model_selection, "OllamaClient", StubClient)
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(_report().model_dump(mode="json")), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("HC_OLLAMA_MODEL=qwen3:30b\n", encoding="utf-8")

    selected = select_qualified_model(
        qualification_path=qualification,
        env_path=env,
        settings=Settings(ollama_base_url="http://ollama:11434"),
    )

    assert selected == "qwen3.8:27b-q8_0"
    assert env.read_text(encoding="utf-8") == "HC_OLLAMA_MODEL=qwen3.8:27b-q8_0\n"


def test_digest_change_refuses_selection_without_touching_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        def identity(self, model_name: str) -> ModelIdentity:
            return ModelIdentity(name=model_name, digest="b" * 64)

    monkeypatch.setattr(model_selection, "OllamaClient", StubClient)
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(_report().model_dump(mode="json")), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("HC_OLLAMA_MODEL=qwen3:30b\n", encoding="utf-8")

    with pytest.raises(ModelSelectionError, match="qualified_candidate_digest_changed"):
        select_qualified_model(
            qualification_path=qualification,
            env_path=env,
            settings=Settings(ollama_base_url="http://ollama:11434"),
        )

    assert env.read_text(encoding="utf-8") == "HC_OLLAMA_MODEL=qwen3:30b\n"


def test_rollback_requires_installed_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubClient:
        def __init__(self, _settings: Settings) -> None:
            pass

        def identity(self, _model_name: str) -> None:
            return None

    monkeypatch.setattr(model_selection, "OllamaClient", StubClient)
    env = tmp_path / ".env"
    env.write_text("HC_OLLAMA_MODEL=qwen3.8:27b-q8_0\n", encoding="utf-8")
    with pytest.raises(ModelSelectionError, match="rollback_model_unavailable"):
        select_default_model(
            env_path=env,
            settings=Settings(ollama_base_url="http://ollama:11434"),
        )
    assert env.read_text(encoding="utf-8") == "HC_OLLAMA_MODEL=qwen3.8:27b-q8_0\n"


def test_model_switch_make_targets_use_the_default_compose_topology() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    activation = makefile.split("qwen38-activate:", maxsplit=1)[1].split(
        "qwen3-rollback:", maxsplit=1
    )[0]
    rollback = makefile.split("qwen3-rollback:", maxsplit=1)[1].split("audit:", maxsplit=1)[0]

    for target in (activation, rollback):
        assert "docker compose up -d --force-recreate api worker" in target
        assert "deploy/credentials.compose.yml" not in target
