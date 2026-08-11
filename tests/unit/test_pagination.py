from pathlib import Path

import pytest
from fastapi import HTTPException
from scripts.check_pagination_inventory import audit

from healthcurve.api.pagination import PageRequest, page_metadata


def test_empty_collection_has_one_valid_page() -> None:
    metadata = page_metadata(0, PageRequest(page=1, page_size=25))

    assert metadata.model_dump() == {
        "page": 1,
        "page_size": 25,
        "total_items": 0,
        "total_pages": 1,
    }


def test_page_beyond_last_page_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        page_metadata(25, PageRequest(page=2, page_size=25))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "code": "page_out_of_range",
        "page": 2,
        "total_pages": 1,
    }


def test_pagination_inventory_covers_discovered_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]

    assert audit(root) == []
