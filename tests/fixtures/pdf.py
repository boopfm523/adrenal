"""Private synthetic PDF-tool doubles; never use real medical documents in tests."""

from __future__ import annotations

import json
from pathlib import Path

from healthcurve.document_worker import CommandResult


class QpdfRunner:
    def __init__(self, *, pages: int = 1, inspection: object | None = None, check_code: int = 0):
        self.pages = pages
        self.inspection = inspection if inspection is not None else {"attachments": {}}
        self.check_code = check_code
        self.calls: list[list[str]] = []

    def __call__(
        self, args: list[str], *, timeout: float, stdout_path: Path | None = None
    ) -> CommandResult:
        self.calls.append(args)
        if "--check" in args:
            return CommandResult(self.check_code, "")
        if "--show-npages" in args:
            return CommandResult(0, str(self.pages))
        assert stdout_path is not None
        stdout_path.write_text(json.dumps(self.inspection))
        return CommandResult(0, "")
