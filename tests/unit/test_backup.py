from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from healthcurve.operations.backup import (
    BackupConfig,
    BackupError,
    Toolchain,
    create_backup,
    verify_encrypted_set,
)


class FakeTools:
    def __init__(self, *, fail: str | None = None, omit_schema: str | None = None) -> None:
        self.fail = fail
        self.omit_schema = omit_schema

    def __call__(
        self, args: Sequence[str], _env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if command == "fake-pg-dump":
            if self.fail == "dump":
                return subprocess.CompletedProcess(args, 1, "", "private database error")
            output = Path(
                next(value.split("=", 1)[1] for value in args if value.startswith("--file="))
            )
            output.write_bytes(b"synthetic-postgres-custom-dump")
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == "fake-pg-restore":
            if self.fail == "inventory":
                return subprocess.CompletedProcess(args, 1, "", "private inventory error")
            schemas = ["identity", "fact", "plan", "ai", "ops"]
            inventory = "\n".join(
                f"1; 2615 1 SCHEMA - {schema} healthcurve"
                for schema in schemas
                if schema != self.omit_schema
            )
            return subprocess.CompletedProcess(args, 0, inventory, "")
        if command == "fake-age":
            if self.fail == "age":
                return subprocess.CompletedProcess(args, 1, "", "private encryption error")
            output = Path(args[args.index("--output") + 1])
            source = Path(args[-1])
            output.write_bytes(b"age-encrypted-synthetic\n" + source.read_bytes()[:128])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected fake command: {command}")


TOOLS = Toolchain("fake-pg-dump", "fake-pg-restore", "fake-age")


def _config(tmp_path: Path) -> BackupConfig:
    uploads = tmp_path / "uploads"
    reports = tmp_path / "reports"
    destination = tmp_path / "destination"
    work = tmp_path / "work"
    (uploads / "stored").mkdir(parents=True)
    reports.mkdir()
    (uploads / "stored" / "11111111-2222-4333-8444-555555555555.pdf").write_bytes(
        b"synthetic document"
    )
    (reports / "report.pdf").write_bytes(b"synthetic report")
    restore = tmp_path / "compose.yaml"
    restore.write_text("services: {}\n", encoding="utf-8")
    return BackupConfig(
        destination=destination,
        work_root=work,
        recipient="age1syntheticpublicrecipient",
        artifact_roots={"uploads": uploads, "reports": reports},
        restore_files={"compose-yaml": restore},
    )


def test_synthetic_backup_is_encrypted_finalized_and_verifiable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = create_backup(
        config,
        tools=TOOLS,
        runner=FakeTools(),
        command_env={},
        now=datetime(2026, 8, 9, 3, 0, tzinfo=UTC),
    )

    assert result.archive.read_bytes().startswith(b"age-encrypted-synthetic")
    assert verify_encrypted_set(result.envelope) == result
    envelope = json.loads(result.envelope.read_text(encoding="utf-8"))
    assert envelope["verified"] is True
    assert "recipient" not in envelope
    assert list(config.work_root.iterdir()) == []
    assert not list(config.destination.glob("*.partial"))


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("dump", "database_dump_failed"),
        ("inventory", "database_inventory_failed"),
        ("age", "encryption_failed"),
    ],
)
def test_command_failures_are_redacted_and_remove_plaintext(
    tmp_path: Path, failure: str, reason: str
) -> None:
    config = _config(tmp_path)
    with pytest.raises(BackupError, match=f"^{reason}$"):
        create_backup(config, tools=TOOLS, runner=FakeTools(fail=failure), command_env={})
    assert list(config.work_root.iterdir()) == []
    assert not list(config.destination.glob("*"))


def test_missing_required_schema_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(BackupError, match=r"^database_inventory_incomplete$"):
        create_backup(config, tools=TOOLS, runner=FakeTools(omit_schema="fact"), command_env={})
    assert list(config.work_root.iterdir()) == []


def test_artifact_symlink_is_rejected_and_plaintext_removed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.artifact_roots["uploads"] / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(BackupError, match=r"^artifact_symlink_rejected$"):
        create_backup(config, tools=TOOLS, runner=FakeTools(), command_env={})
    assert list(config.work_root.iterdir()) == []


def test_ciphertext_tampering_fails_verification(tmp_path: Path) -> None:
    result = create_backup(_config(tmp_path), tools=TOOLS, runner=FakeTools(), command_env={})
    result.archive.write_bytes(result.archive.read_bytes() + b"tampered")
    with pytest.raises(BackupError, match=r"^ciphertext_size_mismatch$"):
        verify_encrypted_set(result.envelope)


def test_same_size_ciphertext_tampering_fails_checksum_verification(tmp_path: Path) -> None:
    result = create_backup(_config(tmp_path), tools=TOOLS, runner=FakeTools(), command_env={})
    ciphertext = result.archive.read_bytes()
    result.archive.write_bytes(bytes([ciphertext[0] ^ 1]) + ciphertext[1:])
    with pytest.raises(BackupError, match=r"^ciphertext_checksum_mismatch$"):
        verify_encrypted_set(result.envelope)


def test_internal_tar_contains_database_artifacts_config_and_manifest(tmp_path: Path) -> None:
    captured_tar: Path | None = None

    class InspectingTools(FakeTools):
        def __call__(
            self, args: Sequence[str], env: Mapping[str, str]
        ) -> subprocess.CompletedProcess[str]:
            nonlocal captured_tar
            if args[0] == "fake-age":
                captured_tar = tmp_path / "captured.tar"
                captured_tar.write_bytes(Path(args[-1]).read_bytes())
            return super().__call__(args, env)

    create_backup(_config(tmp_path), tools=TOOLS, runner=InspectingTools(), command_env={})
    assert captured_tar is not None
    with tarfile.open(captured_tar) as archive:
        names = set(archive.getnames())
        manifest_member = archive.extractfile("payload/manifest.json")
        assert manifest_member is not None
        manifest = json.load(manifest_member)
        for component in manifest["components"]:
            member = archive.extractfile(f"payload/{component['path']}")
            assert member is not None
            content = member.read()
            assert len(content) == component["size"]
            assert hashlib.sha256(content).hexdigest() == component["sha256"]
    assert "payload/database.dump" in names
    assert "payload/database.inventory" in names
    assert "payload/artifacts/uploads/stored/11111111-2222-4333-8444-555555555555.pdf" in names
    assert "payload/artifacts/reports/report.pdf" in names
    assert "payload/restore-config/compose-yaml" in names
    assert "payload/manifest.json" in names


def test_missing_tool_is_a_stable_reason_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> None:
        return None

    monkeypatch.setattr("healthcurve.operations.backup.shutil.which", missing)
    with pytest.raises(BackupError, match=r"^tool_missing_pg_dump$"):
        Toolchain.resolve()
