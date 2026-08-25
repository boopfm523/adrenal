"""Fail closed when a generated ADR-0029 static bundle violates its public contract."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = "healthcurve-public-v1"
ISO_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UUID: Final = re.compile(
    rb"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
INLINE_SCRIPT: Final = re.compile(r"<script([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
FORBIDDEN_KEYS: Final = frozenset(
    {
        "owner_id",
        "email",
        "display_name",
        "notes",
        "correction_reason",
        "supersedes_id",
        "provider_id",
        "source_id",
        "source_revision_sha256",
        "revision_id",
        "recorded_at",
    }
)
ALLOWED_SUFFIXES: Final = frozenset(
    {"", ".css", ".html", ".js", ".json", ".png", ".woff", ".woff2"}
)
TEXT_SUFFIXES: Final = frozenset({"", ".css", ".html", ".js", ".json"})


def keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from keys(child)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def verify(directory: Path) -> tuple[int, int, str]:
    root = directory.resolve(strict=True)
    for path in (root, *root.rglob("*")):
        mode = path.stat().st_mode
        if path.is_dir() and not mode & stat.S_IXOTH:
            relative = path.relative_to(root)
            raise ValueError(f"static directory is not publicly traversable: {relative}")
        if path.is_file() and not mode & stat.S_IROTH:
            raise ValueError(f"static file is not publicly readable: {path.relative_to(root)}")
    index = root / "index.html"
    manifest_path = root / "data" / "manifest.json"
    publication_path = root / "data" / "publication.json"
    for required in (index, manifest_path, publication_path):
        if not required.is_file():
            raise ValueError(f"required static file is missing: {required.relative_to(root)}")

    html = index.read_text(encoding="utf-8")
    for required_text in (
        "Content-Security-Policy",
        "form-action 'none'",
        "connect-src 'self'",
        "https://*.google-analytics.com",
        "https://*.analytics.google.com",
        "https://www.googletagmanager.com/gtag/js?id=G-M7EL70V6DE",
        "gtag('config', 'G-M7EL70V6DE')",
        '<div id="root"></div>',
    ):
        if required_text not in html:
            raise ValueError(f"index.html is missing required boundary: {required_text}")
    if "<form" in html.lower():
        raise ValueError("public index contains a form")
    for attributes, body in INLINE_SCRIPT.findall(html):
        if "src=" in attributes.lower() or not body.strip():
            continue
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        if f"'sha256-{digest}'" not in html:
            raise ValueError("public index contains an inline script without its CSP hash")

    manifest = read_json(manifest_path)
    dates = manifest.get("dates")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or not isinstance(dates, list)
        or not dates
        or any(not isinstance(day, str) or ISO_DATE.fullmatch(day) is None for day in dates)
        or dates != sorted(set(dates))
        or manifest.get("newest_date") != dates[-1]
    ):
        raise ValueError("public manifest failed validation")

    expected_days = {f"{day}.json" for day in dates}
    actual_days = {path.name for path in (root / "data" / "days").glob("*.json")}
    if actual_days != expected_days:
        raise ValueError("published day files do not exactly match the manifest")

    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root)
        if path.name != ".htaccess" and path.suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"unsupported static file type: {relative}")
        content = path.read_bytes()
        total_bytes += len(content)
        digest.update(relative.as_posix().encode())
        digest.update(content)
        if path.suffix in TEXT_SUFFIXES and UUID.search(content) is not None:
            raise ValueError(f"UUID-shaped private identifier found in {relative}")
        if path.suffix == ".json":
            value = read_json(path)
            forbidden = FORBIDDEN_KEYS.intersection(keys(value))
            if forbidden:
                raise ValueError(f"forbidden public keys in {relative}: {sorted(forbidden)}")
            if path.parent.name == "days":
                expected_date = path.stem
                if (
                    value.get("schema_version") != SCHEMA_VERSION
                    or value.get("date") != expected_date
                    or value.get("timezone") != manifest.get("timezone")
                ):
                    raise ValueError(f"day contract mismatch in {relative}")
        if path.suffix in {".html", ".js"} and any(
            marker in content
            for marker in (b"/api/v1", b'credentials:"include"', b"http://localhost")
        ):
            raise ValueError(f"private application dependency found in {relative}")
    return len(files), total_bytes, digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    count, size, digest = verify(args.directory)
    print(f"public_bundle_valid files={count} bytes={size} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
