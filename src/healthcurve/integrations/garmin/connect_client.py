"""Narrow read-only boundary around the unofficial python-garminconnect client."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from garminconnect import Garmin
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)


class GarminReadClient(Protocol):
    """Initial provider operations used by the daily sync (ADR-0012)."""

    def login(self) -> None: ...

    def get_stats(self, day: str) -> dict[str, Any]: ...

    def get_sleep_data(self, day: str) -> dict[str, Any]: ...

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]: ...

    def logout(self) -> None: ...


class GarminIntradayReadClient(GarminReadClient, Protocol):
    """Expanded read-only operations approved for intraday sync by ADR-0014."""

    def get_heart_rates(self, day: str) -> dict[str, Any]: ...

    def get_stress_data(self, day: str) -> dict[str, Any]: ...

    def get_respiration_data(self, day: str) -> dict[str, Any]: ...

    def get_hrv_data(self, day: str) -> dict[str, Any]: ...

    def get_steps_data(self, day: str) -> list[dict[str, Any]]: ...


class GarminProviderError(RuntimeError):
    """A stable privacy-safe failure code; provider text is intentionally discarded."""

    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


class PythonGarminReadClient:
    """Adapter that makes mutating methods unreachable to application code."""

    def __init__(
        self,
        *,
        email: str | None,
        password: str | None,
        token_store: Path,
        prompt_mfa: Callable[[], str] | None = None,
    ) -> None:
        self._token_store = validate_token_store_path(token_store)
        self._client = Garmin(
            email,
            password,
            prompt_mfa=prompt_mfa,
            return_on_mfa=prompt_mfa is None,
            retry_attempts=2,
            retry_min_wait=1,
            retry_max_wait=8,
        )

    def login(self) -> None:
        validate_token_store_directory(self._token_store)
        prior_umask = os.umask(0o077)
        try:
            needs_mfa, _ = self._client.login(str(self._token_store))
        except Exception as exc:  # library has several strategy-specific subclasses
            raise _safe_error(exc) from None
        finally:
            os.umask(prior_umask)
        if needs_mfa is not None:
            raise GarminProviderError("garmin_mfa_required", retryable=False)
        validate_token_store_permissions(self._token_store)

    def get_stats(self, day: str) -> dict[str, Any]:
        return self._read(lambda: self._client.get_stats(day), dict)

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        return self._read(lambda: self._client.get_sleep_data(day), dict)

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
        return self._read(lambda: self._client.get_activities_by_date(start, end), list)

    def get_heart_rates(self, day: str) -> dict[str, Any]:
        return self._read(lambda: self._client.get_heart_rates(day), dict)

    def get_stress_data(self, day: str) -> dict[str, Any]:
        return self._read(lambda: self._client.get_stress_data(day), dict)

    def get_respiration_data(self, day: str) -> dict[str, Any]:
        return self._read(lambda: self._client.get_respiration_data(day), dict)

    def get_hrv_data(self, day: str) -> dict[str, Any]:
        # Garmin legitimately returns null when the device has no HRV data for a day.
        try:
            value: Any = self._client.get_hrv_data(day)
        except Exception as exc:
            raise _safe_error(exc) from None
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise GarminProviderError("garmin_response_shape_changed", retryable=False)
        return value

    def get_steps_data(self, day: str) -> list[dict[str, Any]]:
        return self._read(lambda: self._client.get_steps_data(day), list)

    def logout(self) -> None:
        try:
            self._client.logout(str(self._token_store))
        except Exception as exc:
            raise _safe_error(exc) from None

    @staticmethod
    def _read[T: (dict[str, Any], list[dict[str, Any]])](
        call: Callable[[], Any], expected: type[T]
    ) -> T:
        try:
            value = call()
        except Exception as exc:
            raise _safe_error(exc) from None
        if not isinstance(value, expected):
            raise GarminProviderError("garmin_response_shape_changed", retryable=False)
        return value


def validate_token_store_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise GarminProviderError("garmin_token_path_not_absolute", retryable=False)
    if expanded.is_symlink() or any(parent.is_symlink() for parent in expanded.parents):
        raise GarminProviderError("garmin_token_path_symlink", retryable=False)
    if any((candidate / ".git").is_dir() for candidate in (expanded, *expanded.parents)):
        raise GarminProviderError("garmin_token_path_in_repository", retryable=False)
    return expanded


def validate_token_store_directory(path: Path) -> None:
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise GarminProviderError("garmin_token_store_missing", retryable=False)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise GarminProviderError("garmin_token_store_permissions", retryable=False)


def validate_token_store_permissions(path: Path) -> None:
    validate_token_store_directory(path)
    token = path / "garmin_tokens.json"
    if not token.is_file() or token.is_symlink() or stat.S_IMODE(token.stat().st_mode) != 0o600:
        raise GarminProviderError("garmin_token_file_permissions", retryable=False)


def _safe_error(exc: Exception) -> GarminProviderError:
    if isinstance(exc, GarminProviderError):
        return exc
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return GarminProviderError("garmin_rate_limited", retryable=True)
    if isinstance(exc, GarminConnectAuthenticationError):
        return GarminProviderError("garmin_authentication_required", retryable=False)
    if isinstance(exc, GarminConnectConnectionError):
        return GarminProviderError("garmin_provider_unavailable", retryable=True)
    return GarminProviderError("garmin_client_failed", retryable=False)
