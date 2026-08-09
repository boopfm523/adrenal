from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.reports.rendering import RenderedReport
from healthcurve.reports.storage import (
    ArtifactStorageError,
    delete_owner_artifacts,
    read,
    store,
)


def snapshot() -> ReportSnapshot:
    return ReportSnapshot(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 2),
        timezone="UTC",
        selected_sections=["metrics"],
        include_ai=False,
        source_manifest={"fact": [], "plan": [], "patient_note": [], "ai": []},
        metric_values={},
        snapshot_content={"fact": [], "plan": [], "patient_note": [], "ai": []},
        render_version="report-v1",
        canonical_sha256="0" * 64,
    )


def test_artifacts_are_private_checksummed_and_owner_deletable(tmp_path: Path) -> None:
    report = snapshot()
    bundle = RenderedReport(
        html=b"<html></html>", pdf=b"%PDF-synthetic", csv=b"a,b\n", json=b"{}\n"
    )
    with Session() as session:
        artifacts = store(
            session,
            root=tmp_path,
            snapshot=report,
            rendered=bundle,
            companion_formats={"csv", "json"},
        )
    assert {artifact.format for artifact in artifacts} == {"pdf", "csv", "json"}
    for artifact in artifacts:
        path = tmp_path / artifact.relative_path
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert read(tmp_path, artifact)

    pdf = next(artifact for artifact in artifacts if artifact.format == "pdf")
    (tmp_path / pdf.relative_path).write_bytes(b"tampered")
    with pytest.raises(ArtifactStorageError, match="integrity"):
        read(tmp_path, pdf)

    delete_owner_artifacts(tmp_path, report.owner_id)
    assert not (tmp_path / str(report.owner_id)).exists()


def test_artifact_path_cannot_escape_private_root(tmp_path: Path) -> None:
    artifact = ReportArtifact(
        snapshot_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        format="pdf",
        media_type="application/pdf",
        relative_path="../outside.pdf",
        sha256="0" * 64,
        byte_size=1,
    )
    with pytest.raises(ArtifactStorageError, match="escapes"):
        read(tmp_path, artifact)
