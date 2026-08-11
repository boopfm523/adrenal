#!/usr/bin/env python3
"""Reject unclassified or stale record-list and table pagination surfaces."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import cast

_EXPLICIT_COLLECTION_ROUTES = {
    "routers/data_quality.py:/data-quality",
    "routers/events.py:/timeline",
    "routers/garmin.py:/records",
}
_ALLOWED_STATUSES = {"paginated", "pending", "bounded_reference"}


def _list_routes(root: Path) -> set[str]:
    router_root = root / "src/healthcurve/api/routers"
    routes = set(_EXPLICIT_COLLECTION_ROUTES)
    for path in sorted(router_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("list_"):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "get"
                    and decorator.args
                ):
                    continue
                route = decorator.args[0]
                if isinstance(route, ast.Constant) and isinstance(route.value, str):
                    relative = path.relative_to(router_root.parent).as_posix()
                    routes.add(f"{relative}:{route.value}")
    return routes


def _table_files(root: Path) -> set[str]:
    frontend = root / "frontend/src"
    return {
        path.relative_to(root).as_posix()
        for path in frontend.rglob("*.tsx")
        if "<table" in path.read_text(encoding="utf-8")
    }


def _entries(value: object) -> dict[str, dict[str, str]]:
    return cast(dict[str, dict[str, str]], value)


def audit(root: Path) -> list[str]:
    inventory = cast(
        dict[str, object],
        json.loads((root / "docs/pagination-inventory.json").read_text(encoding="utf-8")),
    )
    api = _entries(inventory["api_collections"])
    tables = _entries(inventory["frontend_tables"])
    additional = _entries(inventory["additional_ui_collections"])
    discovered_api = _list_routes(root)
    discovered_tables = _table_files(root)

    failures = [
        *(f"unclassified_api_collection:{name}" for name in sorted(discovered_api - api.keys())),
        *(f"stale_api_classification:{name}" for name in sorted(api.keys() - discovered_api)),
        *(
            f"unclassified_frontend_table:{name}"
            for name in sorted(discovered_tables - tables.keys())
        ),
        *(
            f"stale_frontend_classification:{name}"
            for name in sorted(tables.keys() - discovered_tables)
        ),
    ]
    for group_name, entries in (("api", api), ("frontend", tables), ("additional", additional)):
        for name, entry in sorted(entries.items()):
            status = entry.get("status", "")
            if status not in _ALLOWED_STATUSES:
                failures.append(f"invalid_status:{group_name}:{name}")
            if status in {"paginated", "pending"} and not entry.get("issue", "").startswith("hc-"):
                failures.append(f"missing_issue:{group_name}:{name}")
            if status == "bounded_reference" and not entry.get("reason"):
                failures.append(f"missing_bounded_reason:{group_name}:{name}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = audit(root)
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        return 1
    print("pagination inventory covers every discovered list route and frontend table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
