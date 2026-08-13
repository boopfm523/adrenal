import json
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


def test_pagination_inventory_rejects_new_mapped_card_history(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    page = tmp_path / "frontend/src/pages/NewHistory.tsx"
    page.parent.mkdir(parents=True)
    page.write_text(
        "export function NewHistory() { return rows.map((row) => "
        "<article key={row.id}>{row.name}</article>); }",
        encoding="utf-8",
    )
    api_entry = {
        "status": "paginated",
        "issue": "hc-test",
        "pagination_contract": "PageRequest",
        "date_filter": "experienced_local",
        "timezone": "explicit_iana",
        "sensitivity": "owner_scoped_health",
    }
    inventory = {
        "api_collections": {
            "routers/data_quality.py:/data-quality": api_entry,
            "routers/events.py:/timeline": api_entry,
            "routers/garmin.py:/records": api_entry,
            "routers/garmin.py:/samples": api_entry,
        },
        "frontend_tables": {},
        "frontend_mapped_card_files": {},
        "additional_ui_collections": {},
    }
    (tmp_path / "docs/pagination-inventory.json").write_text(
        json.dumps(inventory), encoding="utf-8"
    )

    assert audit(tmp_path) == [
        "unclassified_frontend_mapped_cards:frontend/src/pages/NewHistory.tsx"
    ]


def test_pagination_inventory_discovers_mantine_tables(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    page = tmp_path / "frontend/src/pages/NewTable.tsx"
    page.parent.mkdir(parents=True)
    page.write_text(
        'import { Table } from "@mantine/core"; '
        'export function NewTable() { return <Table className="records" />; }',
        encoding="utf-8",
    )
    api_entry = {
        "status": "paginated",
        "issue": "hc-test",
        "pagination_contract": "PageRequest",
        "date_filter": "experienced_local",
        "timezone": "explicit_iana",
        "sensitivity": "owner_scoped_health",
    }
    inventory = {
        "api_collections": {
            "routers/data_quality.py:/data-quality": api_entry,
            "routers/events.py:/timeline": api_entry,
            "routers/garmin.py:/records": api_entry,
            "routers/garmin.py:/samples": api_entry,
        },
        "frontend_tables": {},
        "frontend_mapped_card_files": {},
        "additional_ui_collections": {},
    }
    (tmp_path / "docs/pagination-inventory.json").write_text(
        json.dumps(inventory), encoding="utf-8"
    )

    assert audit(tmp_path) == ["unclassified_frontend_table:frontend/src/pages/NewTable.tsx"]
