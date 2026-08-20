from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from scripts.check_history_secrets import check_history, scan_history


def _git_executable() -> str:
    executable = shutil.which("git")
    assert executable is not None
    return executable


GIT = _git_executable()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed executable; arguments are test-controlled.
        [GIT, "-C", str(repo), *args], check=True, capture_output=True
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "synthetic@example.invalid")
    _git(repo, "config", "user.name", "Synthetic Test")
    return repo


def _reviews(path: Path, reviews: dict[str, object]) -> Path:
    path.write_text(json.dumps({"version": 1, "reviews": reviews}), encoding="utf-8")
    return path


def _synthetic_telegram_token() -> str:
    return "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


def test_finds_secret_removed_from_worktree_and_never_reports_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    token = _synthetic_telegram_token()
    tracked = repo / "settings.env"
    tracked.write_text(f"TELEGRAM_BOT_TOKEN={token}\n", encoding="utf-8")
    _git(repo, "add", "settings.env")
    _git(repo, "commit", "-qm", "synthetic secret")
    tracked.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "remove synthetic secret")

    reviews = _reviews(tmp_path / "reviews.json", {})
    failures = check_history(repo, reviews)

    assert failures
    assert "UNREVIEWED settings.env:1 (Telegram Bot Token" in failures[0]
    assert "review " in failures[0]
    assert token not in failures[0]
    assert "<redacted candidate line>" in failures[0]


def test_reviewed_false_positive_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / "fixture.txt"
    tracked.write_text(
        f"TELEGRAM_BOT_TOKEN={_synthetic_telegram_token()}\n",
        encoding="utf-8",
    )
    _git(repo, "add", "fixture.txt")
    _git(repo, "commit", "-qm", "synthetic fixture")
    finding = scan_history(repo)[0]
    reviews = _reviews(
        tmp_path / "reviews.json",
        {
            finding.review_key: {
                "is_secret": False,
                "reason": "Synthetic regression fixture",
            }
        },
    )

    assert check_history(repo, reviews) == []


def test_current_tree_baseline_false_positive_is_reused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / "fixture.txt"
    tracked.write_text(
        f"TELEGRAM_BOT_TOKEN={_synthetic_telegram_token()}\n",
        encoding="utf-8",
    )
    _git(repo, "add", "fixture.txt")
    _git(repo, "commit", "-qm", "synthetic fixture")
    finding = scan_history(repo)[0]
    (repo / ".secrets.baseline").write_text(
        json.dumps(
            {
                "results": {
                    "fixture.txt": [
                        {
                            "type": finding.secret_type,
                            "hashed_secret": finding.secret_hash,
                            "is_secret": False,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert check_history(repo, _reviews(tmp_path / "reviews.json", {})) == []


def test_confirmed_secret_fails_without_printing_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    token = _synthetic_telegram_token()
    tracked = repo / "settings.env"
    tracked.write_text(f"TELEGRAM_BOT_TOKEN={token}\n", encoding="utf-8")
    _git(repo, "add", "settings.env")
    _git(repo, "commit", "-qm", "synthetic secret")
    finding = scan_history(repo)[0]
    reviews = _reviews(
        tmp_path / "reviews.json",
        {
            finding.review_key: {
                "is_secret": True,
                "reason": "Synthetic confirmed secret",
            }
        },
    )

    failures = check_history(repo, reviews)
    assert len(failures) == 1
    assert failures[0].startswith(
        f"CONFIRMED settings.env:1 (Telegram Bot Token; blob {finding.blob_oid[:12]}; "
        f"review {finding.review_key}; 1 occurrence(s)); remove and rotate it"
    )
    assert token not in failures[0]


def test_identical_candidate_across_history_is_reported_once(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    token = _synthetic_telegram_token()
    tracked = repo / "settings.env"
    tracked.write_text(f"TELEGRAM_BOT_TOKEN={token}\nREVISION=1\n", encoding="utf-8")
    _git(repo, "add", "settings.env")
    _git(repo, "commit", "-qm", "first synthetic fixture")
    tracked.write_text(f"TELEGRAM_BOT_TOKEN={token}\nREVISION=2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "second synthetic fixture")

    failures = check_history(repo, _reviews(tmp_path / "reviews.json", {}))

    assert len(failures) == 1
    assert "2 occurrence(s)" in failures[0]
    assert token not in failures[0]


def test_scanner_metadata_is_not_scanned_as_a_candidate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / ".secrets.baseline"
    tracked.write_text(
        json.dumps({"hashed_secret": _synthetic_telegram_token()}) + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".secrets.baseline")
    _git(repo, "commit", "-qm", "synthetic scanner metadata")

    assert scan_history(repo) == []
