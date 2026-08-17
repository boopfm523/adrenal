"""Scan every reachable Git blob without printing candidate secret values."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEWS = ROOT / ".secrets.history-reviews.json"
EXCLUDED_SOURCE_PATHS = {".secrets.baseline", ".secrets.history-reviews.json", "uv.lock"}
EXCLUDED_SOURCE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
GIT = shutil.which("git")
if GIT is None:  # pragma: no cover - Git is required before the script can run.
    raise RuntimeError("git executable is unavailable")


@dataclass(frozen=True)
class Finding:
    blob_oid: str
    path: str
    line_number: int
    secret_type: str
    secret_hash: str
    redacted_excerpt: str

    @property
    def review_key(self) -> str:
        # The same candidate commonly appears in many historical versions. Bind
        # reviews to the detector type and one-way candidate hash so one explicit
        # review covers identical bytes without exposing their value.
        value = f"{self.secret_type}\0{self.secret_hash}"
        return hashlib.sha256(value.encode()).hexdigest()


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(  # noqa: S603 - fixed executable; args are internal.
        [GIT, "-C", str(repo), *args],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        message = completed.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"git command failed ({args[0]}): {message}")
    return completed.stdout


def reachable_blobs(repo: Path) -> list[tuple[str, str]]:
    objects: list[tuple[str, str]] = []
    for raw_line in _git(repo, "rev-list", "--objects", "--all").splitlines():
        oid_raw, separator, path_raw = raw_line.partition(b" ")
        oid = oid_raw.decode("ascii")
        if _git(repo, "cat-file", "-t", oid).strip() != b"blob":
            continue
        path = path_raw.decode(errors="replace") if separator else "<unnamed>"
        objects.append((oid, path))
    return objects


def _safe_suffix(path: str) -> str:
    suffix = Path(path).suffix
    if not suffix or len(suffix) > 16 or not suffix[1:].isalnum():
        return ".txt"
    return suffix


def _redact_line(raw: bytes, line_number: int) -> str:
    lines = raw.decode(errors="replace").splitlines()
    if line_number < 1 or line_number > len(lines):
        return "<line unavailable>"
    return "<redacted candidate line>"


def scan_history(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="healthcurve-history-secrets-") as temp:
        temp_root = Path(temp)
        scan_root = temp_root / "blobs"
        scan_root.mkdir()
        baseline_path = temp_root / "scan-baseline.json"
        baseline = json.loads((ROOT / ".secrets.baseline").read_text(encoding="utf-8"))
        baseline["results"] = {}
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        mapping: dict[str, tuple[str, str]] = {}
        for index, (oid, source_path) in enumerate(reachable_blobs(repo)):
            if (
                source_path in EXCLUDED_SOURCE_PATHS
                or Path(source_path).name in EXCLUDED_SOURCE_NAMES
                or source_path.startswith(".beads/")
            ):
                continue
            scan_name = f"blob-{index:06d}{_safe_suffix(source_path)}"
            (scan_root / scan_name).write_bytes(_git(repo, "cat-file", "blob", oid))
            mapping[scan_name] = (oid, source_path)

        completed = subprocess.run(  # noqa: S603 - fixed interpreter and arguments.
            [
                sys.executable,
                "-m",
                "detect_secrets",
                "-C",
                str(scan_root),
                "-c",
                "8",
                "scan",
                "--all-files",
                "--no-verify",
                "--baseline",
                str(baseline_path),
                ".",
            ],
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            message = completed.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"detect-secrets history scan failed: {message}")
        results = json.loads(baseline_path.read_text(encoding="utf-8")).get("results", {})
        for raw_name, secrets in results.items():
            scan_name = raw_name.removeprefix("./")
            oid, source_path = mapping[scan_name]
            raw = (scan_root / scan_name).read_bytes()
            for secret in secrets:
                line_number = int(secret["line_number"])
                findings.append(
                    Finding(
                        blob_oid=oid,
                        path=source_path,
                        line_number=line_number,
                        secret_type=secret["type"],
                        secret_hash=secret["hashed_secret"],
                        redacted_excerpt=_redact_line(raw, line_number),
                    )
                )
    return sorted(
        findings,
        key=lambda item: (item.path, item.line_number, item.secret_type, item.blob_oid),
    )


def _load_reviews(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("reviews"), dict):
        raise ValueError("history secret review file is malformed")
    return data["reviews"]


def _load_current_tree_reviews(repo: Path) -> dict[str, dict[str, Any]]:
    """Reuse false-positive decisions already recorded by detect-secrets.

    The current-tree baseline is itself a reviewed artifact. Historical-only
    candidates still require an entry in the dedicated history review file.
    """
    baseline_path = repo / ".secrets.baseline"
    if not baseline_path.is_file():
        return {}
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    reviews: dict[str, dict[str, Any]] = {}
    for entries in data.get("results", {}).values():
        for entry in entries:
            if entry.get("is_secret") is not False:
                continue
            value = f"{entry['type']}\0{entry['hashed_secret']}"
            review_key = hashlib.sha256(value.encode()).hexdigest()
            reviews[review_key] = {
                "is_secret": False,
                "reason": "Reviewed in the current-tree detect-secrets baseline",
            }
    return reviews


def check_history(repo: Path, reviews_path: Path) -> list[str]:
    reviews = _load_current_tree_reviews(repo)
    # Dedicated history decisions intentionally take precedence so a candidate
    # can be escalated to confirmed even if an older baseline called it false.
    reviews.update(_load_reviews(reviews_path))
    failures: list[str] = []
    grouped: dict[str, list[Finding]] = {}
    for finding in scan_history(repo):
        grouped.setdefault(finding.review_key, []).append(finding)
    for review_key, occurrences in sorted(grouped.items()):
        finding = occurrences[0]
        review = reviews.get(finding.review_key)
        location = f"{finding.path}:{finding.line_number}"
        metadata = (
            f"{finding.secret_type}; blob {finding.blob_oid[:12]}; "
            f"review {review_key}; {len(occurrences)} occurrence(s)"
        )
        if review is None:
            failures.append(
                f"UNREVIEWED {location} ({metadata}); excerpt: {finding.redacted_excerpt}"
            )
        elif review.get("is_secret") is True:
            failures.append(f"CONFIRMED {location} ({metadata}); remove and rotate it")
        elif review.get("is_secret") is not False or not review.get("reason"):
            failures.append(f"INVALID-REVIEW {location} ({metadata})")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    args = parser.parse_args()
    failures = check_history(args.repo.resolve(), args.reviews.resolve())
    if failures:
        for failure in failures:
            print(f"history secret scan failure: {failure}")
        return 1
    print("history secret scan: every reachable Git blob passed reviewed redacted scanning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
