from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from healthcurve.operations.backup import BackupError
from healthcurve.operations.retention import (
    BackupSet,
    OffsiteSettings,
    RemoteObject,
    cleanup_local,
    discover_backup_sets,
    plan_retention,
    upload_backup_set,
)


def _set(
    tmp_path: Path,
    index: int,
    when: datetime,
    *,
    complete: bool = True,
    verified: bool = True,
) -> BackupSet:
    set_id = f"hc-test-{index:03d}"
    archive = tmp_path / f"{set_id}.tar.age"
    archive.write_bytes(f"encrypted-{index}".encode())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    envelope = tmp_path / f"{set_id}.json"
    envelope.write_text(
        json.dumps(
            {
                "format_version": 1,
                "set_id": set_id,
                "created_at": when.isoformat(),
                "archive": archive.name,
                "size": archive.stat().st_size,
                "sha256": checksum,
                "verified": verified,
            }
        ),
        encoding="utf-8",
    )
    return BackupSet(
        set_id,
        when,
        archive,
        envelope,
        archive.stat().st_size,
        checksum,
        complete,
        verified,
    )


def test_retention_selects_daily_weekly_and_monthly_boundaries(tmp_path: Path) -> None:
    start = datetime(2025, 1, 1, 12, tzinfo=UTC)
    catalog = [_set(tmp_path, index, start + timedelta(days=index)) for index in range(400)]
    plan = plan_retention(catalog)
    retained = {item.set_id for item in plan.retain}

    newest_by_day = {item.created_at.date(): item for item in catalog}
    expected_daily = {newest_by_day[day].set_id for day in sorted(newest_by_day, reverse=True)[:7]}
    newest_by_week = {
        (item.created_at.isocalendar().year, item.created_at.isocalendar().week): item
        for item in catalog
    }
    expected_weekly = {
        newest_by_week[week].set_id for week in sorted(newest_by_week, reverse=True)[:5]
    }
    newest_by_month = {(item.created_at.year, item.created_at.month): item for item in catalog}
    expected_monthly = {
        newest_by_month[month].set_id for month in sorted(newest_by_month, reverse=True)[:12]
    }
    assert retained == expected_daily | expected_weekly | expected_monthly


def test_incomplete_and_unverified_sets_never_satisfy_buckets(tmp_path: Path) -> None:
    when = datetime(2026, 8, 9, tzinfo=UTC)
    invalid = [
        _set(tmp_path, 1, when + timedelta(hours=2), complete=False),
        _set(tmp_path, 2, when + timedelta(hours=1), verified=False),
    ]
    good = _set(tmp_path, 3, when)
    plan = plan_retention([*invalid, good])
    assert plan.retain == (good,)
    assert {item.set_id for item in plan.protected} == {"hc-test-001", "hc-test-002"}
    assert not plan.delete


def test_last_known_good_is_always_retained(tmp_path: Path) -> None:
    only = _set(tmp_path, 1, datetime(2026, 8, 9, tzinfo=UTC))
    plan = plan_retention([only])
    assert plan.retain == (only,)
    assert not plan.delete


def test_day_buckets_are_utc_not_source_offset(tmp_path: Path) -> None:
    same_utc_day = [
        _set(tmp_path, 1, datetime(2026, 8, 9, 23, 30, tzinfo=UTC)),
        _set(tmp_path, 2, datetime(2026, 8, 10, 1, 0, tzinfo=timezone(timedelta(hours=2)))),
    ]
    plan = plan_retention(same_utc_day)
    assert {item.set_id for item in plan.retain} == {"hc-test-001"}


def test_cleanup_defaults_to_dry_run_and_apply_reverifies(tmp_path: Path) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    catalog = [_set(tmp_path, index, start + timedelta(days=index)) for index in range(500)]
    plan = plan_retention(catalog)
    candidates = cleanup_local(plan)
    assert candidates
    assert all(item.archive.exists() and item.envelope.exists() for item in plan.delete)

    assert cleanup_local(plan, apply=True) == candidates
    assert all(not item.archive.exists() and not item.envelope.exists() for item in plan.delete)
    assert all(item.archive.exists() and item.envelope.exists() for item in plan.retain)


def test_cleanup_refuses_a_changed_candidate(tmp_path: Path) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    plan = plan_retention(
        [_set(tmp_path, index, start + timedelta(days=index)) for index in range(500)]
    )
    changed = plan.delete[0]
    changed.archive.write_bytes(changed.archive.read_bytes() + b"changed")
    with pytest.raises(BackupError, match=r"^ciphertext_size_mismatch$"):
        cleanup_local(plan, apply=True)
    assert changed.archive.exists() and changed.envelope.exists()


def test_discovery_protects_invalid_envelopes_and_orphan_archives(tmp_path: Path) -> None:
    good = _set(tmp_path, 1, datetime(2026, 8, 9, tzinfo=UTC))
    invalid = _set(tmp_path, 2, datetime(2026, 8, 8, tzinfo=UTC))
    invalid.envelope.write_text("not-json", encoding="utf-8")
    orphan = tmp_path / "hc-orphan.tar.age"
    orphan.write_bytes(b"incomplete")

    found, protected = discover_backup_sets(tmp_path)
    assert found == (good,)
    assert set(protected) == {invalid.envelope, invalid.archive, orphan}


class FakeOffsite:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, RemoteObject]] = {}
        self.puts = 0
        self.corrupt_after_put = False

    def head(self, key: str) -> RemoteObject | None:
        stored = self.objects.get(key)
        return stored[1] if stored else None

    def put_if_absent(self, key: str, source: Path, metadata: Mapping[str, str]) -> None:
        if key in self.objects:
            return
        content = source.read_bytes()
        checksum = metadata["sha256"]
        if self.corrupt_after_put:
            checksum = "0" * 64
        self.objects[key] = (content, RemoteObject(len(content), checksum))
        self.puts += 1


def test_offsite_upload_is_idempotent_and_verifies_metadata(tmp_path: Path) -> None:
    backup_set = _set(tmp_path, 1, datetime(2026, 8, 9, tzinfo=UTC))
    store = FakeOffsite()
    assert upload_backup_set(store, backup_set, prefix="healthcurve") == 2
    assert upload_backup_set(store, backup_set, prefix="healthcurve") == 0
    assert store.puts == 2
    assert set(store.objects) == {
        f"healthcurve/{backup_set.archive.name}",
        f"healthcurve/{backup_set.envelope.name}",
    }


def test_offsite_upload_fails_on_conflict_or_bad_post_upload_metadata(tmp_path: Path) -> None:
    backup_set = _set(tmp_path, 1, datetime(2026, 8, 9, tzinfo=UTC))
    conflict = FakeOffsite()
    conflict.objects[backup_set.archive.name] = (b"wrong", RemoteObject(5, "0" * 64))
    with pytest.raises(BackupError, match=r"^offsite_object_conflict$"):
        upload_backup_set(conflict, backup_set)

    corrupt = FakeOffsite()
    corrupt.corrupt_after_put = True
    with pytest.raises(BackupError, match=r"^offsite_verification_failed$"):
        upload_backup_set(corrupt, backup_set)


def test_offsite_is_disabled_by_default_and_enabled_requires_configuration() -> None:
    assert OffsiteSettings.from_env({}) == OffsiteSettings(enabled=False)
    with pytest.raises(BackupError, match=r"^offsite_configuration_incomplete$"):
        OffsiteSettings.from_env({"HC_BACKUP_OFFSITE_ENABLED": "true"})
    with pytest.raises(BackupError, match=r"^offsite_enabled_invalid$"):
        OffsiteSettings.from_env({"HC_BACKUP_OFFSITE_ENABLED": "perhaps"})


def test_offsite_credential_must_be_private_and_separate(tmp_path: Path) -> None:
    credential = tmp_path / "routine.credentials"
    credential.write_text("synthetic-only", encoding="utf-8")
    credential.chmod(0o600)
    env = {
        "HC_BACKUP_OFFSITE_ENABLED": "true",
        "HC_BACKUP_OFFSITE_PROVIDER": "fake-test-provider",
        "HC_BACKUP_OFFSITE_DESTINATION": "synthetic-bucket/prefix",
        "HC_BACKUP_OFFSITE_CREDENTIAL_FILE": str(credential),
    }
    settings = OffsiteSettings.from_env(env)
    assert settings.enabled and settings.credential_file == credential

    with pytest.raises(BackupError, match=r"^offsite_maintenance_credential_forbidden$"):
        OffsiteSettings.from_env(
            {**env, "HC_BACKUP_OFFSITE_MAINTENANCE_CREDENTIAL_FILE": str(credential)}
        )

    credential.chmod(0o644)
    with pytest.raises(BackupError, match=r"^offsite_credential_file_unsafe$"):
        OffsiteSettings.from_env(env)
