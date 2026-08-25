from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from scripts.verify_public_healthcurve_bundle import SCHEMA_VERSION, verify


def synthetic_bundle(root: Path) -> Path:
    root.chmod(0o755)
    (root / "assets").mkdir(parents=True)
    (root / "data" / "days").mkdir(parents=True)
    analytics = "gtag('config', 'G-M7EL70V6DE')"
    analytics_hash = base64.b64encode(hashlib.sha256(analytics.encode()).digest()).decode()
    (root / "index.html").write_text(
        f"""<!doctype html>
        <meta http-equiv="Content-Security-Policy"
          content="script-src 'self'; script-src-elem 'self' 'sha256-{analytics_hash}' https://www.googletagmanager.com;
          img-src 'self' https://*.google-analytics.com https://www.googletagmanager.com;
          connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com
          https://www.googletagmanager.com; form-action 'none'">
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-M7EL70V6DE"></script>
        <script>{analytics}</script>
        <div id="root"></div><script src="/healthcurve/assets/app.js"></script>""",
        encoding="utf-8",
    )
    (root / "assets" / "app.js").write_text("console.log('synthetic static app')", encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "timezone": "America/New_York",
        "newest_date": "2026-08-23",
        "dates": ["2026-08-23"],
    }
    (root / "data" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "data" / "publication.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8"
    )
    (root / "data" / "days" / "2026-08-23.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "date": "2026-08-23",
                "timezone": "America/New_York",
                "curve": {"series_name": "Synthetic"},
            }
        ),
        encoding="utf-8",
    )
    return root


def test_synthetic_static_bundle_passes(tmp_path: Path) -> None:
    count, size, digest = verify(synthetic_bundle(tmp_path))
    assert count == 5
    assert size > 0
    assert len(digest) == 64


def test_bundle_verifier_allows_bundled_png_assets(tmp_path: Path) -> None:
    root = synthetic_bundle(tmp_path)
    (root / "assets" / "healthcurve-logo.png").write_bytes(
        b"\x89PNG\r\n\x1a\n30000000-0000-4000-8000-000000000003"
    )

    count, size, digest = verify(root)

    assert count == 6
    assert size > 0
    assert len(digest) == 64


def test_bundle_verifier_rejects_non_public_directory_mode(tmp_path: Path) -> None:
    root = synthetic_bundle(tmp_path)
    (root / "data").chmod(0o700)
    with pytest.raises(ValueError, match="not publicly traversable"):
        verify(root)


def test_bundle_verifier_requires_configured_google_analytics_tag(tmp_path: Path) -> None:
    root = synthetic_bundle(tmp_path)
    index = root / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace("G-M7EL70V6DE", "G-REMOVED"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"googletagmanager\.com/gtag/js"):
        verify(root)


def test_bundle_verifier_rejects_inline_script_hash_drift(tmp_path: Path) -> None:
    root = synthetic_bundle(tmp_path)
    index = root / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "gtag('config', 'G-M7EL70V6DE')</script>",
            "gtag('config', 'G-M7EL70V6DE');</script>",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inline script without its CSP hash"):
        verify(root)


@pytest.mark.parametrize(
    ("relative", "content", "message"),
    [
        ("data/days/2026-08-23.json", '{"notes":"private"}', "forbidden public keys"),
        ("assets/app.js", 'fetch("/api/v1/symptoms")', "private application dependency"),
        (
            "data/days/2026-08-23.json",
            '{"id":"30000000-0000-4000-8000-000000000003"}',
            "UUID-shaped",
        ),
    ],
)
def test_bundle_verifier_rejects_private_material(
    tmp_path: Path, relative: str, content: str, message: str
) -> None:
    root = synthetic_bundle(tmp_path)
    (root / relative).write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        verify(root)
