"""Private synthetic PDF-tool doubles; never use real medical documents in tests."""

from __future__ import annotations

import json
from pathlib import Path

from healthcurve.document_worker import CommandResult


def synthetic_text_lab_pdf() -> bytes:
    """A generated one-page PDF with synthetic table text and no patient data."""
    commands = "\n".join(
        [
            "BT /F1 10 Tf 1 0 0 1 50 770 Tm (Synthetic laboratory panel) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 50 740 Tm (Analyte) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 220 740 Tm (Value) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 310 740 Tm (Unit) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 390 740 Tm (Reference Range) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 50 710 Tm (Synthetic sodium) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 220 710 Tm (140) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 310 710 Tm (mmol/L) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 390 710 Tm (135-145) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 50 680 Tm (Synthetic unparsed note) Tj ET",
        ]
    ).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(commands)).encode() + b" >>\nstream\n" + commands + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(offsets)}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(payload)


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
