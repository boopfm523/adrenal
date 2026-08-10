"""Run a privacy-safe encrypted synthetic round trip through an rclone Drive remote."""

from __future__ import annotations

import argparse
import hashlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

if __package__:
    from scripts.check_rclone_drive_config import REMOTE_NAME, check_config
else:
    from check_rclone_drive_config import REMOTE_NAME, check_config

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(args), check=False, capture_output=True)  # noqa: S603


def _valid_destination(destination: str) -> bool:
    if ":" not in destination:
        return False
    remote, path = destination.split(":", 1)
    parts = PurePosixPath(path).parts
    return bool(
        REMOTE_NAME.fullmatch(remote)
        and path
        and not path.startswith("/")
        and ".." not in parts
        and not any(ord(character) < 32 or ord(character) == 127 for character in path)
    )


def verify_roundtrip(
    config: Path,
    destination: str,
    *,
    runner: Runner = _run,
    rclone: str | None = None,
    age: str | None = None,
    age_keygen: str | None = None,
) -> str | None:
    """Return one fixed failure code, or ``None`` on success."""
    if check_config(config):
        return "oauth_config_invalid"
    if not _valid_destination(destination):
        return "destination_invalid"
    tools = {
        "rclone": rclone or shutil.which("rclone"),
        "age": age or shutil.which("age"),
        "age-keygen": age_keygen or shutil.which("age-keygen"),
    }
    if any(value is None for value in tools.values()):
        return "required_tool_missing"

    with tempfile.TemporaryDirectory(prefix="hc-oauth-probe-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        identity = root / "identity.txt"
        source = root / "synthetic.txt"
        encrypted = root / "synthetic.txt.age"
        downloaded = root / "downloaded.age"
        restored = root / "restored.txt"
        source.write_bytes(b"HealthCurve synthetic OAuth probe\n" + secrets.token_bytes(32))
        source.chmod(0o600)

        generated = runner((str(tools["age-keygen"]), "-o", str(identity)))
        if generated.returncode != 0:
            return "synthetic_identity_generation_failed"
        recipient_result = runner((str(tools["age-keygen"]), "-y", str(identity)))
        recipient = recipient_result.stdout.decode("ascii", errors="ignore").strip()
        if recipient_result.returncode != 0 or not recipient.startswith("age1"):
            return "synthetic_recipient_generation_failed"
        encrypted_result = runner(
            (
                str(tools["age"]),
                "-r",
                recipient,
                "-o",
                str(encrypted),
                str(source),
            )
        )
        if encrypted_result.returncode != 0 or not encrypted.is_file():
            return "synthetic_encryption_failed"

        object_name = f"hc-oauth-cutover-{uuid.uuid4().hex}.age"
        remote = f"{destination.rstrip('/')}/{object_name}"
        rclone_base = (
            str(tools["rclone"]),
            "--config",
            str(config),
            "--log-level",
            "ERROR",
        )
        uploaded = runner(
            (*rclone_base, "copyto", str(encrypted), remote, "--immutable", "--no-traverse")
        )
        if uploaded.returncode != 0:
            return "synthetic_upload_failed"
        downloaded_result = runner(
            (*rclone_base, "copyto", remote, str(downloaded), "--immutable", "--no-traverse")
        )
        if downloaded_result.returncode != 0 or not downloaded.is_file():
            return "synthetic_download_failed"
        if _sha256(encrypted) != _sha256(downloaded):
            return "synthetic_ciphertext_mismatch"

        decrypted = runner(
            (
                str(tools["age"]),
                "-d",
                "-i",
                str(identity),
                "-o",
                str(restored),
                str(downloaded),
            )
        )
        if decrypted.returncode != 0 or not restored.is_file():
            return "synthetic_decryption_failed"
        if source.read_bytes() != restored.read_bytes():
            return "synthetic_plaintext_mismatch"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify encrypted synthetic upload/download without printing private data."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--destination",
        default="healthcurve-drive:HealthCurve Backups",
    )
    args = parser.parse_args(argv)
    failure = verify_roundtrip(args.config, args.destination)
    if failure is not None:
        print(f"rclone encrypted round trip failure: {failure}", file=sys.stderr)
        return 1
    print("rclone encrypted round trip: verified; immutable synthetic probe retained remotely")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
