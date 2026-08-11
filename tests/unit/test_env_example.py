from pathlib import Path

from scripts.check_env_example import audit


def test_env_example_covers_supported_configuration() -> None:
    root = Path(__file__).resolve().parents[2]

    assert audit(root) == []
