#!/usr/bin/env python3
"""Independent availability probe; run this on a different host.

Environment:
  HC_TARGET_URL       e.g. https://healthcurve.example/health/ready
  HC_ALERT_URL        ntfy-compatible private topic URL
  HC_STATE_FILE       local state path (default ./healthcurve-monitor.state)
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return response.status == 200 and response.read(1_024) == b'{"status":"ok"}'
    except (urllib.error.URLError, TimeoutError):
        return False


def _notify(url: str, message: str) -> None:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=message.encode(),
        method="POST",
        headers={"Title": "HealthCurve monitor", "Priority": "urgent", "Tags": "warning"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        if response.status >= 300:
            raise RuntimeError("alert receiver rejected notification")


def main() -> int:
    target = os.environ.get("HC_TARGET_URL", "").strip()
    alert_url = os.environ.get("HC_ALERT_URL", "").strip()
    state_file = Path(os.environ.get("HC_STATE_FILE", "healthcurve-monitor.state"))
    if not target.startswith("https://") or not alert_url.startswith("https://"):
        print("HC_TARGET_URL and HC_ALERT_URL must both use HTTPS", file=sys.stderr)
        return 1

    healthy = _probe(target)
    current = "healthy" if healthy else "unavailable"
    previous = state_file.read_text().strip() if state_file.exists() else "unknown"
    if current != previous:
        message = (
            "HealthCurve is reachable again."
            if healthy
            else "HealthCurve is unavailable from the independent monitor."
        )
        _notify(alert_url, message)
    state_file.write_text(current, encoding="utf-8")
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
