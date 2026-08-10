"""Private synthetic PDF-tool doubles; never use real medical documents in tests."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


def synthetic_scanned_lab_pdf() -> bytes:
    """One image-only PDF with large high-contrast synthetic laboratory text."""
    image = Image.new("RGB", (1800, 1100), "white")
    draw = ImageDraw.Draw(image)
    # Pillow bundles its default font, so the fixture does not depend on a host font.
    font = ImageFont.load_default(size=44)
    rows = [
        ("Analyte", "Value", "Unit", "Range"),
        ("Synthetic sodium", "140", "mmol/L", "135-145"),
        ("Synthetic unclear note", "", "", ""),
    ]
    y = 180
    for row in rows:
        for x, value in zip((80, 700, 1020, 1350), row, strict=True):
            if value:
                draw.text((x, y), value, fill="black", font=font)
        y += 180
    output = BytesIO()
    image.save(output, format="PDF", resolution=150)
    return output.getvalue()


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
        if args[0] == "pdftoppm":
            Image.new("RGB", (1200, 800), "white").save(f"{args[-1]}.png")
            return CommandResult(0, "")
        if "--check" in args:
            return CommandResult(self.check_code, "")
        if "--show-npages" in args:
            return CommandResult(0, str(self.pages))
        assert stdout_path is not None
        stdout_path.write_text(json.dumps(self.inspection))
        return CommandResult(0, "")


class OcrToolRunner(QpdfRunner):
    def __init__(self, *, width: int = 1200, height: int = 800, tsv: str | None = None) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self.tsv = tsv

    def __call__(
        self, args: list[str], *, timeout: float, stdout_path: Path | None = None
    ) -> CommandResult:
        if args[0] == "pdftoppm":
            self.calls.append(args)
            Image.new("RGB", (self.width, self.height), "white").save(f"{args[-1]}.png")
            return CommandResult(0, "")
        if args[0] == "tesseract":
            self.calls.append(args)
            assert stdout_path is not None
            stdout_path.write_text(self.tsv or _synthetic_tesseract_tsv())
            return CommandResult(0, "")
        return super().__call__(args, timeout=timeout, stdout_path=stdout_path)


def _synthetic_tesseract_tsv() -> str:
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    words = [
        (1, 1, 1, 1, 1, 1, 80, 100, 170, 45, 99, "Analyte"),
        (1, 1, 1, 1, 1, 2, 700, 100, 120, 45, 99, "Value"),
        (1, 1, 1, 1, 1, 3, 1020, 100, 90, 45, 99, "Unit"),
        (1, 1, 1, 1, 1, 4, 1350, 100, 130, 45, 99, "Range"),
        (1, 1, 1, 1, 2, 1, 80, 280, 210, 45, 96, "Synthetic"),
        (1, 1, 1, 1, 2, 2, 300, 280, 160, 45, 96, "sodium"),
        (1, 1, 1, 1, 2, 3, 700, 280, 90, 45, 94, "140"),
        (1, 1, 1, 1, 2, 4, 1020, 280, 150, 45, 95, "mmol/L"),
        (1, 1, 1, 1, 2, 5, 1350, 280, 180, 45, 93, "135-145"),
        (1, 1, 1, 1, 3, 1, 80, 460, 180, 45, 42, "Unclear"),
        (1, 1, 1, 1, 3, 2, 270, 460, 100, 45, 45, "note"),
    ]
    return "\n".join([header, *("\t".join(str(value) for value in row) for row in words)])
