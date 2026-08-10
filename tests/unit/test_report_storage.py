from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from healthcurve.operations.jobs import JobQueueError
from healthcurve.reports.cleanup_jobs import make_snapshot_artifact_cleanup_handler
from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.reports.rendering import RenderedReport
from healthcurve.reports.storage import (
    ArtifactStorageError,
    delete_owner_artifacts,
    delete_snapshot_artifacts,
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


def test_snapshot_cleanup_tombstones_only_the_selected_bundle(tmp_path: Path) -> None:
    first = snapshot()
    second = snapshot()
    second.owner_id = first.owner_id
    bundle = RenderedReport(
        html=b"<html></html>", pdf=b"%PDF-synthetic", csv=b"a,b\n", json=b"{}\n"
    )
    with Session() as session:
        store(session, root=tmp_path, snapshot=first, rendered=bundle, companion_formats=set())
        store(session, root=tmp_path, snapshot=second, rendered=bundle, companion_formats=set())

    delete_snapshot_artifacts(tmp_path, owner_id=first.owner_id, snapshot_id=first.id)
    delete_snapshot_artifacts(tmp_path, owner_id=first.owner_id, snapshot_id=first.id)

    assert not (tmp_path / str(first.owner_id) / str(first.id)).exists()
    assert (tmp_path / str(second.owner_id) / str(second.id)).is_dir()
    assert (tmp_path / ".tombstones" / str(first.owner_id) / f"{first.id}.deleted").is_file()


def test_snapshot_cleanup_refuses_symlink_alias(tmp_path: Path) -> None:
    owner_id = uuid.uuid4()
    target_snapshot_id = uuid.uuid4()
    alias_snapshot_id = uuid.uuid4()
    target = tmp_path / str(owner_id) / str(target_snapshot_id)
    target.mkdir(parents=True)
    retained = target / "report.pdf"
    retained.write_bytes(b"must remain")
    (tmp_path / str(owner_id) / str(alias_snapshot_id)).symlink_to(
        target,
        target_is_directory=True,
    )

    with pytest.raises(ArtifactStorageError, match="symlink"):
        delete_snapshot_artifacts(
            tmp_path,
            owner_id=owner_id,
            snapshot_id=alias_snapshot_id,
        )

    assert retained.read_bytes() == b"must remain"


def test_report_cleanup_handler_retries_with_privacy_safe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = snapshot()
    handler = make_snapshot_artifact_cleanup_handler(tmp_path)

    def fail(_root: Path, *, owner_id: uuid.UUID, snapshot_id: uuid.UUID) -> None:
        del owner_id, snapshot_id
        raise OSError("synthetic private report path")

    monkeypatch.setattr("healthcurve.reports.cleanup_jobs.delete_snapshot_artifacts", fail)
    with pytest.raises(JobQueueError, match=r"^report_artifact_cleanup_failed$") as error:
        handler(
            Mock(spec=Session),
            {"owner_id": str(report.owner_id), "snapshot_id": str(report.id)},
        )
    assert "private report" not in str(error.value)
