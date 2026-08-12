from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from healthcurve.private_exports.storage import (
    AtomicExportWriter,
    PrivateExportStorageError,
    available_path,
    delete,
    inspect,
    resolve,
)


def test_streamed_artifact_is_private_checksummed_and_write_once(tmp_path: Path) -> None:
    owner_id = uuid.uuid4()
    export_id = uuid.uuid4()
    writer = AtomicExportWriter(tmp_path, owner_id=owner_id, export_id=export_id)
    writer.write('{"facts":[')
    writer.write(b'{"id":"one"}')
    writer.write("]}")
    stored = writer.finish()

    path = available_path(
        tmp_path, relative_path=stored.relative_path, expected_size=stored.byte_size
    )
    assert path.read_bytes() == b'{"facts":[{"id":"one"}]}'
    assert inspect(tmp_path, stored.relative_path) == stored
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700

    replay = AtomicExportWriter(tmp_path, owner_id=owner_id, export_id=export_id)
    replay.write("different retry bytes")
    assert replay.finish() == stored
    assert path.read_bytes() == b'{"facts":[{"id":"one"}]}'


def test_partial_and_escaping_artifacts_are_never_exposed(tmp_path: Path) -> None:
    writer = AtomicExportWriter(tmp_path, owner_id=uuid.uuid4(), export_id=uuid.uuid4())
    writer.write("partial private health data")
    assert not writer.target.exists()
    writer.abort()
    assert not writer.target.exists()
    assert not writer.temporary.exists()

    with pytest.raises(PrivateExportStorageError, match="export_artifact_path_invalid"):
        resolve(tmp_path, "../outside.json")


def test_expiration_delete_is_idempotent_and_contained(tmp_path: Path) -> None:
    writer = AtomicExportWriter(tmp_path, owner_id=uuid.uuid4(), export_id=uuid.uuid4())
    writer.write("{}")
    stored = writer.finish()
    delete(tmp_path, stored.relative_path)
    assert not (tmp_path / stored.relative_path).exists()
    delete(tmp_path, stored.relative_path)
