from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from scripts.verify_rclone_drive_roundtrip import verify_roundtrip


class FakeCommands:
    def __init__(self) -> None:
        self.remote: dict[str, bytes] = {}
        self.fail_tool: str | None = None

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        tool = Path(args[0]).name
        if tool == self.fail_tool:
            return subprocess.CompletedProcess(args, 1, b"", b"private provider details")
        if tool == "age-keygen" and "-o" in args:
            Path(args[args.index("-o") + 1]).write_text("synthetic identity", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if tool == "age-keygen" and "-y" in args:
            return subprocess.CompletedProcess(args, 0, b"age1synthetic\n", b"")
        if tool == "age" and "-r" in args:
            output = Path(args[args.index("-o") + 1])
            source = Path(args[-1])
            output.write_bytes(b"encrypted:" + source.read_bytes())
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if tool == "age" and "-d" in args:
            output = Path(args[args.index("-o") + 1])
            source = Path(args[-1]).read_bytes()
            output.write_bytes(source.removeprefix(b"encrypted:"))
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if tool == "rclone":
            command = args.index("copyto")
            source = args[command + 1]
            destination = args[command + 2]
            if ":" in source:
                Path(destination).write_bytes(self.remote[source])
            else:
                self.remote[destination] = Path(source).read_bytes()
            return subprocess.CompletedProcess(args, 0, b"", b"")
        raise AssertionError(f"unexpected synthetic command: {tool}")


def _config(path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "[healthcurve-drive]",
                "type = drive",
                "client_id = synthetic.apps.googleusercontent.com",
                "client_secret = synthetic-obscured-secret",
                "scope = drive.file",
                'token = {"refresh_token":"synthetic-refresh"}',
                "service_account_file =",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_encrypted_synthetic_roundtrip(tmp_path: Path) -> None:
    fake = FakeCommands()
    failure = verify_roundtrip(
        _config(tmp_path / "rclone.conf"),
        "healthcurve-drive:HealthCurve Backups",
        runner=fake,
        rclone="/synthetic/rclone",
        age="/synthetic/age",
        age_keygen="/synthetic/age-keygen",
    )
    assert failure is None
    assert len(fake.remote) == 1
    assert next(iter(fake.remote.values())).startswith(b"encrypted:")


def test_transport_failure_returns_only_fixed_code(tmp_path: Path) -> None:
    fake = FakeCommands()
    fake.fail_tool = "rclone"
    failure = verify_roundtrip(
        _config(tmp_path / "rclone.conf"),
        "healthcurve-drive:HealthCurve Backups",
        runner=fake,
        rclone="/synthetic/rclone",
        age="/synthetic/age",
        age_keygen="/synthetic/age-keygen",
    )
    assert failure == "synthetic_upload_failed"
    assert "private" not in failure


def test_invalid_destination_fails_before_commands(tmp_path: Path) -> None:
    fake = FakeCommands()
    failure = verify_roundtrip(
        _config(tmp_path / "rclone.conf"),
        "healthcurve-drive:../escape",
        runner=fake,
        rclone="/synthetic/rclone",
        age="/synthetic/age",
        age_keygen="/synthetic/age-keygen",
    )
    assert failure == "destination_invalid"
    assert fake.remote == {}
