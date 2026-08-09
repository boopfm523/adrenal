from __future__ import annotations

import json
from pathlib import Path

import pytest

from healthcurve.integrations.credentials import (
    CredentialConfigurationError,
    CredentialKeyRing,
    add_active_key,
    create_key_file,
    retire_key,
)


def test_key_file_is_owner_only_and_repr_hides_material(tmp_path: Path) -> None:
    path = tmp_path / "credential-keys.json"
    create_key_file(path, "key_2026_08")

    assert path.stat().st_mode & 0o777 == 0o600
    ring = CredentialKeyRing.from_file(path)
    encoded_key = json.loads(path.read_text())["keys"]["key_2026_08"]
    assert ring.active_key_id == "key_2026_08"
    assert encoded_key not in repr(ring)


def test_key_file_creation_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "credential-keys.json"
    create_key_file(path, "key_one")
    original = path.read_bytes()
    with pytest.raises(CredentialConfigurationError, match="credential_key_file_exists"):
        create_key_file(path, "key_two")
    assert path.read_bytes() == original


def test_group_or_world_readable_key_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "credential-keys.json"
    create_key_file(path, "key_one")
    path.chmod(0o640)
    with pytest.raises(
        CredentialConfigurationError, match="credential_key_file_permissions_unsafe"
    ):
        CredentialKeyRing.from_file(path)


def test_add_key_is_atomic_and_retains_old_decryption_key(tmp_path: Path) -> None:
    path = tmp_path / "credential-keys.json"
    create_key_file(path, "key_one")
    old = CredentialKeyRing.from_file(path).key("key_one")

    add_active_key(path, "key_two")
    ring = CredentialKeyRing.from_file(path)

    assert ring.active_key_id == "key_two"
    assert ring.key_ids == {"key_one", "key_two"}
    assert ring.key("key_one") == old
    assert ring.key("key_two") != old
    assert path.stat().st_mode & 0o777 == 0o600

    retire_key(path, "key_one")
    retired = CredentialKeyRing.from_file(path)
    assert retired.key_ids == {"key_two"}
    with pytest.raises(CredentialConfigurationError, match="credential_active_key_cannot_retire"):
        retire_key(path, "key_two")


@pytest.mark.parametrize("label", ["UPPER", "1first", "space here", "", "a" * 65])
def test_key_ids_are_safe_bounded_labels(tmp_path: Path, label: str) -> None:
    with pytest.raises(CredentialConfigurationError, match="credential_key_id_invalid"):
        create_key_file(tmp_path / "keys", label)
