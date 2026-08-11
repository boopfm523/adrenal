from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from healthcurve.operations.backup import BackupError
from healthcurve.operations.restore import RestoreTools, validated_restore_payload

SET_ID = "hc-20260810T210718Z-1234abcd"
RECIPIENT = "age1syntheticrestoreidentity"
FINGERPRINT = hashlib.sha256(RECIPIENT.encode()).hexdigest()[:16]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture(
    tmp_path: Path,
    *,
    recipient: str = RECIPIENT,
    component_checksum_override: str | None = None,
    extra_file: bool = False,
    omit_restore_file: bool = False,
    omit_canary: bool = False,
    unsafe_member: bool = False,
) -> tuple[Path, Path, Path, bytes, str]:
    source = tmp_path / "source"
    payload = source / "payload"
    (payload / "artifacts" / "uploads").mkdir(parents=True)
    (payload / "artifacts" / "reports").mkdir(parents=True)
    (payload / "restore-config").mkdir()
    files = {
        "database.dump": b"synthetic custom-format database",
        "database.inventory": b"synthetic inventory",
        "restore-config/alembic-ini": b"[alembic]\n",
        "restore-config/caddyfile": b"synthetic caddy config\n",
        "restore-config/docker-compose-yml": b"services: {}\n",
        "restore-config/restore-canary-json": (
            b'{"format_version":1,"kind":"healthcurve_restore_canary","synthetic":true}\n'
        ),
        "artifacts/uploads/synthetic.pdf": b"synthetic document",
        "artifacts/reports/synthetic.pdf": b"synthetic report",
    }
    if omit_restore_file:
        files.pop("restore-config/caddyfile")
    if omit_canary:
        files.pop("restore-config/restore-canary-json")
    for relative, content in files.items():
        path = payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if extra_file:
        (payload / "unmanifested.txt").write_text("extra", encoding="utf-8")
    components = [
        {
            "path": relative,
            "size": len(content),
            "sha256": (
                component_checksum_override
                if component_checksum_override and relative == "database.dump"
                else _sha256(content)
            ),
        }
        for relative, content in sorted(files.items())
    ]
    manifest = {
        "format_version": 1,
        "set_id": SET_ID,
        "created_at": datetime(2026, 8, 10, 21, 7, 18, tzinfo=UTC).isoformat(),
        "required_schemas": ["identity", "fact", "plan", "ai", "ops"],
        "recipient_fingerprint": FINGERPRINT,
        "components": components,
    }
    (payload / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    tar_path = tmp_path / "payload.tar"
    with tarfile.open(tar_path, mode="w") as archive:
        archive.add(payload, arcname="payload")
        if unsafe_member:
            member = tarfile.TarInfo("payload/unsafe-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            archive.addfile(member, io.BytesIO())
    tar_bytes = tar_path.read_bytes()

    destination = tmp_path / "backup"
    destination.mkdir()
    encrypted = destination / f"{SET_ID}.tar.age"
    encrypted.write_bytes(b"x" * (len(tar_bytes) + 128))
    envelope = destination / f"{SET_ID}.json"
    envelope.write_text(
        json.dumps(
            {
                "format_version": 1,
                "set_id": SET_ID,
                "created_at": manifest["created_at"],
                "archive": encrypted.name,
                "size": encrypted.stat().st_size,
                "sha256": _sha256(encrypted.read_bytes()),
                "recipient_fingerprint": FINGERPRINT,
                "verified": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    identity = tmp_path / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-SYNTHETIC\n", encoding="utf-8")
    identity.chmod(0o600)
    work = tmp_path / "work"
    return envelope, identity, work, tar_bytes, recipient


class FakeRestoreCommands:
    def __init__(self, tar_bytes: bytes, recipient: str) -> None:
        self.tar_bytes = tar_bytes
        self.recipient = recipient

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "fake-age-keygen":
            return subprocess.CompletedProcess(args, 0, self.recipient + "\n", "")
        if args[0] == "fake-age":
            output = Path(args[args.index("--output") + 1])
            output.write_bytes(self.tar_bytes)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected command: {args[0]}")


TOOLS = RestoreTools(age="fake-age", age_keygen="fake-age-keygen")


def _open(fixture: tuple[Path, Path, Path, bytes, str]):
    envelope, identity, work, tar_bytes, recipient = fixture
    return validated_restore_payload(
        envelope,
        identity,
        work,
        tools=TOOLS,
        runner=FakeRestoreCommands(tar_bytes, recipient),
    )


def test_validated_payload_verifies_every_component_and_cleans_plaintext(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with _open(fixture) as payload:
        assert payload.set_id == SET_ID
        assert payload.database_dump.read_bytes() == b"synthetic custom-format database"
        assert payload.component_count == 8
        assert payload.uploads.is_dir()
        assert payload.reports.is_dir()
        assert payload.restore_canary.is_file()
        assert payload.root.exists()
    assert fixture[2].is_dir()
    assert list(fixture[2].iterdir()) == []


def test_wrong_recovery_identity_fails_before_plaintext_exists(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, recipient="age1wrongrestoreidentity")
    with pytest.raises(BackupError, match=r"^restore_identity_fingerprint_mismatch$"):
        with _open(fixture):
            pass
    assert not fixture[2].exists()


def test_world_readable_recovery_identity_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1].chmod(0o644)
    with pytest.raises(BackupError, match=r"^restore_identity_permissions_not_owner_only$"):
        with _open(fixture):
            pass


def test_manifest_checksum_failure_removes_plaintext(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, component_checksum_override="0" * 64)
    with pytest.raises(BackupError, match=r"^restore_component_checksum_mismatch$"):
        with _open(fixture):
            pass
    assert list(fixture[2].iterdir()) == []


def test_unmanifested_file_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, extra_file=True)
    with pytest.raises(BackupError, match=r"^restore_manifest_file_set_mismatch$"):
        with _open(fixture):
            pass


def test_missing_required_restore_configuration_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, omit_restore_file=True)
    with pytest.raises(BackupError, match=r"^restore_manifest_required_component_missing$"):
        with _open(fixture):
            pass


def test_missing_restore_canary_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, omit_canary=True)
    with pytest.raises(BackupError, match=r"^restore_manifest_required_component_missing$"):
        with _open(fixture):
            pass


def test_symlink_archive_member_is_rejected_and_removed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, unsafe_member=True)
    with pytest.raises(BackupError, match=r"^restore_archive_unsafe$"):
        with _open(fixture):
            pass
    assert list(fixture[2].iterdir()) == []
