from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from healthcurve.operations.backup import BackupError
from healthcurve.operations.rclone_drive import RcloneDriveWriter, writer_from_settings
from healthcurve.operations.retention import OffsiteSettings, RemoteObject


class FakeRclone:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, ...]] = []
        self.fail_with: OSError | None = None

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self.fail_with is not None:
            raise self.fail_with
        call = tuple(args)
        self.calls.append(call)
        command = call[call.index("ERROR") + 1]
        if command == "lsjson":
            remote = call[call.index(command) + 1]
            content = self.objects.get(remote)
            if content is None:
                return subprocess.CompletedProcess(call, 3, "", "sensitive remote error")
            return subprocess.CompletedProcess(
                call,
                0,
                json.dumps({"IsDir": False, "Size": len(content)}),
                "",
            )
        if command == "cat":
            remote = call[call.index(command) + 1]
            content = self.objects.get(remote)
            if content is None:
                return subprocess.CompletedProcess(call, 3, "", "sensitive remote error")
            return subprocess.CompletedProcess(call, 0, content.decode(), "")
        if command == "copyto":
            source = Path(call[call.index(command) + 1])
            remote = call[call.index(command) + 2]
            if remote in self.objects:
                return subprocess.CompletedProcess(call, 9, "", "immutable conflict")
            self.objects[remote] = source.read_bytes()
            return subprocess.CompletedProcess(call, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


def _writer(tmp_path: Path, fake: FakeRclone) -> RcloneDriveWriter:
    config = tmp_path / "rclone.conf"
    config.write_text("synthetic", encoding="utf-8")
    return RcloneDriveWriter(
        config_file=config,
        executable="/synthetic/rclone",
        runner=fake,
    )


def test_upload_is_immutable_idempotent_and_verifiable(tmp_path: Path) -> None:
    fake = FakeRclone()
    writer = _writer(tmp_path, fake)
    source = tmp_path / "set.tar.age"
    source.write_bytes(b"synthetic-ciphertext")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    key = "healthcurve-drive:HealthCurve Backups/set.tar.age"

    writer.put_if_absent(key, source, {"sha256": checksum})
    assert writer.head(key) == RemoteObject(source.stat().st_size, checksum)
    first_call_count = len(fake.calls)
    writer.put_if_absent(key, source, {"sha256": checksum})
    assert len(fake.calls) == first_call_count + 2
    assert not hasattr(writer, "delete")


def test_interrupted_sidecar_first_upload_can_resume(tmp_path: Path) -> None:
    fake = FakeRclone()
    writer = _writer(tmp_path, fake)
    source = tmp_path / "set.json"
    source.write_text("{}", encoding="utf-8")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    key = "healthcurve-drive:HealthCurve Backups/set.json"
    fake.objects[key + ".hc-sha256.json"] = json.dumps({"sha256": checksum}).encode()

    assert writer.head(key) is None
    writer.put_if_absent(key, source, {"sha256": checksum})
    assert writer.head(key) == RemoteObject(2, checksum)


def test_conflicts_invalid_paths_and_transport_fail_closed(tmp_path: Path) -> None:
    fake = FakeRclone()
    writer = _writer(tmp_path, fake)
    source = tmp_path / "set"
    source.write_bytes(b"content")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    key = "healthcurve-drive:HealthCurve Backups/set"
    fake.objects[key + ".hc-sha256.json"] = json.dumps({"sha256": "0" * 64}).encode()

    with pytest.raises(BackupError, match=r"^offsite_object_conflict$"):
        writer.put_if_absent(key, source, {"sha256": checksum})
    with pytest.raises(BackupError, match=r"^offsite_destination_invalid$"):
        writer.head("healthcurve-drive:../escape")
    with pytest.raises(BackupError, match=r"^offsite_destination_invalid$"):
        writer.head("healthcurve-drive:/absolute")

    fake.fail_with = OSError("credential content must remain private")
    with pytest.raises(BackupError, match=r"^offsite_transport_failed$") as error:
        writer.head(key)
    assert "credential" not in str(error.value)


def test_factory_is_disabled_by_default_and_rejects_unknown_provider(tmp_path: Path) -> None:
    assert writer_from_settings(OffsiteSettings(enabled=False)) is None
    with pytest.raises(BackupError, match=r"^offsite_provider_unsupported$"):
        writer_from_settings(
            OffsiteSettings(
                enabled=True,
                provider="unknown",
                destination="remote:path",
                credential_file=tmp_path / "credential",
            )
        )
    with pytest.raises(BackupError, match=r"^offsite_configuration_incomplete$"):
        writer_from_settings(
            OffsiteSettings(
                enabled=True,
                provider="rclone-google-drive",
                destination="remote:path",
            )
        )
