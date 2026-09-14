"""Local chat model listing and per-conversation choice (ADR-0036). Synthetic HTTP only."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from healthcurve.ai.ollama import LocalChatModel, OllamaClient
from healthcurve.chat import jobs as chat_jobs
from healthcurve.config import Settings

Handler = Callable[[httpx.Request], httpx.Response]

TAGS: dict[str, Any] = {
    "models": [
        {"name": "synthetic-default:27b", "digest": "a" * 64, "details": {"parameter_size": "27B"}},
        {"name": "synthetic-coder:30b", "digest": "b" * 64, "details": {"parameter_size": "30B"}},
        {"name": "synthetic-embed:latest", "digest": "c" * 64},
        {"name": "synthetic-big:120b-cloud", "digest": "d" * 64},
        {"name": "synthetic-proxy:70b", "digest": "e" * 64, "remote_host": "https://ollama.test"},
    ]
}
CAPABILITIES: dict[str, list[str]] = {
    "synthetic-default:27b": ["completion", "tools", "thinking", "vision"],
    "synthetic-coder:30b": ["completion", "tools"],
    "synthetic-embed:latest": ["embedding"],
}
CODER = LocalChatModel(
    name="synthetic-coder:30b", digest="b" * 64, parameter_size="30B", thinking=False, vision=False
)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "ollama_base_url": "http://ollama:11434",
        "ollama_model": "synthetic-default:27b",
    }
    base.update(overrides)
    return Settings(**base)


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    def fake_init(self: httpx.Client, **kwargs: Any) -> None:
        original(self, transport=httpx.MockTransport(handler), **kwargs)

    original = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__", fake_init)


def test_chat_models_lists_only_local_tool_calling_models(monkeypatch: pytest.MonkeyPatch) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            return httpx.Response(200, json={"capabilities": CAPABILITIES[name]})
        raise AssertionError(f"unexpected Ollama request {request.url.path}")

    _patch_transport(monkeypatch, handler)
    models = OllamaClient(_settings()).chat_models()

    assert models == (
        CODER,
        LocalChatModel(
            name="synthetic-default:27b",
            digest="a" * 64,
            parameter_size="27B",
            thinking=True,
            vision=True,
        ),
    )
    # Cloud and remote models are skipped before any metadata request, and nothing loads.
    assert paths.count("/api/show") == 3
    assert "/api/chat" not in paths and "/api/generate" not in paths


def test_chat_models_is_none_when_ollama_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic outage", request=request)

    _patch_transport(monkeypatch, handler)
    assert OllamaClient(_settings()).chat_models() is None


class _FakeClient:
    def __init__(self, installed: tuple[LocalChatModel, ...] | None) -> None:
        self.installed = installed
        self.bound: list[str] = []

    def chat_models(self) -> tuple[LocalChatModel, ...] | None:
        return self.installed

    def for_model(self, model_name: str) -> _FakeClient:
        self.bound.append(model_name)
        return self


def _choose(client: _FakeClient, model_name: str | None, **settings: Any) -> chat_jobs._ModelChoice:  # pyright: ignore[reportPrivateUsage]
    choose = chat_jobs._choose_model  # pyright: ignore[reportPrivateUsage]
    return choose(cast(OllamaClient, client), _settings(**settings), model_name)


def test_default_model_keeps_configured_behavior_without_listing_models() -> None:
    client = _FakeClient(None)
    choice = _choose(client, None, chat_thinking=True)
    assert choice.available and choice.think
    assert choice.client is cast(OllamaClient, client)
    assert client.bound == []


def test_chosen_model_is_rechecked_and_thinks_only_when_supported() -> None:
    client = _FakeClient((CODER,))
    choice = _choose(client, "synthetic-coder:30b", chat_thinking=True)
    assert choice.available
    assert choice.think is False
    assert client.bound == ["synthetic-coder:30b"]

    assert not _choose(_FakeClient((CODER,)), "synthetic-removed:7b").available
    assert not _choose(_FakeClient(None), "synthetic-coder:30b").available
