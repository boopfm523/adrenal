"""Conservative backup retention and write-only offsite copy boundaries.

Only complete, locally verified sets participate in retention.  Invalid or partial
sets are protected for operator investigation rather than being silently deleted.
The routine offsite protocol deliberately has no delete operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from healthcurve.operations.backup import BackupError, verify_encrypted_set


@dataclass(frozen=True)
class BackupSet:
    set_id: str
    created_at: datetime
    archive: Path
    envelope: Path
    size: int
    sha256: str
    complete: bool = True
    verified: bool = True


@dataclass(frozen=True)
class RetentionPlan:
    retain: tuple[BackupSet, ...]
    delete: tuple[BackupSet, ...]
    protected: tuple[BackupSet, ...]


def _newest_per_bucket(
    sets: Sequence[BackupSet], bucket: Callable[[datetime], Hashable], limit: int
) -> set[str]:
    selected: dict[Hashable, BackupSet] = {}
    for backup_set in sorted(sets, key=lambda item: (item.created_at, item.set_id), reverse=True):
        value = bucket(backup_set.created_at)
        if value not in selected and len(selected) < limit:
            selected[value] = backup_set
    return {item.set_id for item in selected.values()}


def plan_retention(sets: Sequence[BackupSet]) -> RetentionPlan:
    """Select 7 daily, 5 ISO-weekly, and 12 monthly recovery points."""
    ids = [item.set_id for item in sets]
    if len(ids) != len(set(ids)):
        raise BackupError("retention_catalog_duplicate")
    for item in sets:
        if item.created_at.tzinfo is None or item.created_at.utcoffset() is None:
            raise BackupError("retention_time_naive")

    eligible = tuple(item for item in sets if item.complete and item.verified)
    protected = tuple(item for item in sets if not (item.complete and item.verified))
    if not eligible:
        return RetentionPlan((), (), tuple(sorted(protected, key=lambda item: item.set_id)))

    keep_ids = _newest_per_bucket(eligible, lambda value: value.astimezone(UTC).date(), 7)
    keep_ids |= _newest_per_bucket(
        eligible,
        lambda value: (
            value.astimezone(UTC).isocalendar().year,
            value.astimezone(UTC).isocalendar().week,
        ),
        5,
    )
    keep_ids |= _newest_per_bucket(
        eligible,
        lambda value: (value.astimezone(UTC).year, value.astimezone(UTC).month),
        12,
    )
    # Explicit invariant: policy changes must never make the newest known-good set
    # deletable, even if every bucket limit is changed later.
    keep_ids.add(max(eligible, key=lambda item: (item.created_at, item.set_id)).set_id)

    retain = tuple(
        sorted(
            (item for item in eligible if item.set_id in keep_ids),
            key=lambda item: (item.created_at, item.set_id),
            reverse=True,
        )
    )
    delete = tuple(
        sorted(
            (item for item in eligible if item.set_id not in keep_ids),
            key=lambda item: (item.created_at, item.set_id),
        )
    )
    return RetentionPlan(
        retain,
        delete,
        tuple(sorted(protected, key=lambda item: (item.created_at, item.set_id))),
    )


def cleanup_local(plan: RetentionPlan, *, apply: bool = False) -> tuple[str, ...]:
    """Return deletion candidates, removing them only with explicit ``apply=True``."""
    candidate_ids = tuple(item.set_id for item in plan.delete)
    if not apply:
        return candidate_ids

    # Validate every candidate before deleting any pair. A changed member therefore
    # cannot cause a partially applied retention pass.
    for item in plan.delete:
        try:
            if item.archive.parent.resolve() != item.envelope.parent.resolve():
                raise BackupError("retention_path_invalid")
            verified = verify_encrypted_set(item.envelope)
            if (
                verified.set_id != item.set_id
                or verified.archive != item.archive
                or verified.sha256 != item.sha256
                or verified.size != item.size
            ):
                raise BackupError("retention_set_changed")
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError("retention_delete_failed") from exc
    for item in plan.delete:
        try:
            item.archive.unlink()
            item.envelope.unlink()
        except OSError as exc:
            raise BackupError("retention_delete_failed") from exc
    return candidate_ids


def load_backup_set(envelope: Path) -> BackupSet:
    """Load a finalized local set, validating filename, metadata, and ciphertext."""
    try:
        data = json.loads(envelope.read_text(encoding="utf-8"))
        set_id = str(data["set_id"])
        if envelope.name != f"{set_id}.json" or data.get("verified") is not True:
            raise BackupError("retention_envelope_invalid")
        created_at = datetime.fromisoformat(str(data["created_at"]))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise BackupError("retention_envelope_invalid")
        verified = verify_encrypted_set(envelope)
        if verified.archive.name != f"{set_id}.tar.age":
            raise BackupError("retention_envelope_invalid")
    except BackupError:
        raise
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError("retention_envelope_invalid") from exc
    return BackupSet(
        set_id=set_id,
        created_at=created_at,
        archive=verified.archive,
        envelope=envelope,
        size=verified.size,
        sha256=verified.sha256,
    )


def discover_backup_sets(directory: Path) -> tuple[tuple[BackupSet, ...], tuple[Path, ...]]:
    """Return verified sets and protected envelopes that require investigation."""
    verified: list[BackupSet] = []
    protected: list[Path] = []
    try:
        envelopes = sorted(directory.glob("hc-*.json"))
        archives = set(directory.glob("hc-*.tar.age"))
    except OSError as exc:
        raise BackupError("retention_catalog_unavailable") from exc
    for envelope in envelopes:
        try:
            item = load_backup_set(envelope)
            verified.append(item)
            archives.discard(item.archive)
        except BackupError:
            protected.append(envelope)
    protected.extend(sorted(archives))
    return tuple(verified), tuple(protected)


@dataclass(frozen=True)
class RemoteObject:
    size: int
    sha256: str


class OffsiteWriter(Protocol):
    """Routine credential capability: inspect metadata and create, never delete."""

    def head(self, key: str) -> RemoteObject | None: ...

    def put_if_absent(self, key: str, source: Path, metadata: Mapping[str, str]) -> None: ...


@dataclass(frozen=True)
class OffsiteSettings:
    enabled: bool
    provider: str | None = None
    destination: str | None = None
    credential_file: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OffsiteSettings:
        values = os.environ if env is None else env
        raw_enabled = values.get("HC_BACKUP_OFFSITE_ENABLED", "false").strip().lower()
        if raw_enabled not in {"true", "false"}:
            raise BackupError("offsite_enabled_invalid")
        if raw_enabled == "false":
            return cls(enabled=False)

        provider = values.get("HC_BACKUP_OFFSITE_PROVIDER", "").strip()
        destination = values.get("HC_BACKUP_OFFSITE_DESTINATION", "").strip()
        credential_value = values.get("HC_BACKUP_OFFSITE_CREDENTIAL_FILE", "").strip()
        if not provider or not destination or not credential_value:
            raise BackupError("offsite_configuration_incomplete")
        credential_file = Path(credential_value)
        try:
            credential_stat = credential_file.stat()
            if (
                not credential_file.is_absolute()
                or not credential_file.is_file()
                or credential_file.is_symlink()
                or credential_stat.st_mode & 0o077
                or credential_stat.st_size == 0
            ):
                raise BackupError("offsite_credential_file_unsafe")
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError("offsite_credential_unavailable") from exc

        maintenance = values.get("HC_BACKUP_OFFSITE_MAINTENANCE_CREDENTIAL_FILE", "").strip()
        if maintenance:
            raise BackupError("offsite_maintenance_credential_forbidden")
        return cls(True, provider, destination, credential_file)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("offsite_source_unavailable") from exc
    return digest.hexdigest()


def _object_key(prefix: str, filename: str) -> str:
    path = PurePosixPath(prefix) / filename if prefix else PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        raise BackupError("offsite_destination_invalid")
    return path.as_posix()


def _ensure_remote(writer: OffsiteWriter, key: str, source: Path, expected: RemoteObject) -> bool:
    try:
        current = writer.head(key)
        if current is not None:
            if current != expected:
                raise BackupError("offsite_object_conflict")
            return False
        writer.put_if_absent(key, source, {"sha256": expected.sha256})
        if writer.head(key) != expected:
            raise BackupError("offsite_verification_failed")
        return True
    except BackupError:
        raise
    except Exception as exc:
        # Providers may place endpoints, account names, or signed URLs in errors.
        raise BackupError("offsite_transport_failed") from exc


def upload_backup_set(writer: OffsiteWriter, backup_set: BackupSet, *, prefix: str = "") -> int:
    """Idempotently upload ciphertext then envelope and verify remote metadata."""
    verified = verify_encrypted_set(backup_set.envelope)
    if (
        not backup_set.complete
        or not backup_set.verified
        or verified.set_id != backup_set.set_id
        or verified.archive != backup_set.archive
        or verified.size != backup_set.size
        or verified.sha256 != backup_set.sha256
    ):
        raise BackupError("offsite_set_invalid")

    archive_key = _object_key(prefix, backup_set.archive.name)
    envelope_key = _object_key(prefix, backup_set.envelope.name)
    uploaded = int(
        _ensure_remote(
            writer,
            archive_key,
            backup_set.archive,
            RemoteObject(backup_set.size, backup_set.sha256),
        )
    )
    try:
        envelope_size = backup_set.envelope.stat().st_size
    except OSError as exc:
        raise BackupError("offsite_source_unavailable") from exc
    uploaded += int(
        _ensure_remote(
            writer,
            envelope_key,
            backup_set.envelope,
            RemoteObject(envelope_size, _sha256(backup_set.envelope)),
        )
    )
    return uploaded


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply local backup retention")
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete eligible sets; omission is always a dry run",
    )
    args = parser.parse_args(argv)
    try:
        sets, protected_paths = discover_backup_sets(args.directory)
        plan = plan_retention(sets)
        candidates = cleanup_local(plan, apply=args.apply)
    except BackupError as exc:
        print(exc.reason_code)
        return 1
    print(
        json.dumps(
            {
                "applied": args.apply,
                "delete": list(candidates),
                "protected_count": len(protected_paths),
                "retain": [item.set_id for item in plan.retain],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
