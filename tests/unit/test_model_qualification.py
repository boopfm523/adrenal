from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from healthcurve.ai.model_qualification import (
    CandidatePreflightError,
    parse_ollama_version,
    run_candidate_preflight,
)
from healthcurve.config import Settings

Handler = Callable[[httpx.Request], httpx.Response]


def _settings() -> Settings:
    return Settings(
        ollama_base_url="http://ollama:11434",
        ollama_model="qwen3:30b",
        ollama_thinking=True,
    )


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    original = httpx.Client.__init__

    def fake_init(self: httpx.Client, **kwargs: Any) -> None:
        original(self, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", fake_init)


def test_preflight_uses_candidate_without_changing_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3.8:27b-q8_0",
                            "digest": "a" * 64,
                        }
                    ]
                },
            )
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "model_digest": "a" * 64,
                "message": {"content": '{"status":"ready"}'},
            },
        )

    _patch_transport(monkeypatch, handler)
    settings = _settings()
    report = run_candidate_preflight(settings)

    assert settings.ollama_model == "qwen3:30b"
    assert report.model_name == "qwen3.8:27b-q8_0"
    assert report.model_digest == "a" * 64
    assert report.thinking_enabled is False
    assert report.context_window == 24_576
    assert requests[0]["model"] == "qwen3.8:27b-q8_0"
    assert requests[0]["think"] is False
    assert requests[0]["options"]["num_ctx"] == 24_576
    assert requests[0]["format"]["properties"]["status"]["const"] == "ready"


@pytest.mark.parametrize("version", ["0.32.15", "invalid"])
def test_preflight_rejects_old_or_invalid_ollama(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": version})

    _patch_transport(monkeypatch, handler)
    with pytest.raises(CandidatePreflightError):
        run_candidate_preflight(_settings())


def test_preflight_rejects_missing_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.0"})
        return httpx.Response(200, json={"models": []})

    _patch_transport(monkeypatch, handler)
    with pytest.raises(CandidatePreflightError, match="candidate_model_missing"):
        run_candidate_preflight(_settings())


def test_parse_ollama_version_accepts_prefix_and_suffix() -> None:
    assert parse_ollama_version("v0.33.0-rc1") == (0, 33, 0)
