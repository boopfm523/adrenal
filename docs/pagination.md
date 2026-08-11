# Record-list pagination

HealthCurve uses one-based, offset-backed pages for interactive record history. The
default page size is 25 and the maximum accepted page size is 100. Collection responses
carry an `items` array and shared page metadata:

```json
{
  "page": {
    "page": 1,
    "page_size": 25,
    "total_items": 0,
    "total_pages": 1
  }
}
```

An empty collection has one empty page. A page number below one, a page size outside
1–100, or a page beyond `total_pages` returns HTTP 422. Every list supplies a stable
chronological order with an ID tie-breaker so page boundaries cannot reorder equal-time
facts. Filter, timezone, sensitive-entry, and sort parameters remain in the browser URL
when the page changes.

The API performs filtering and pagination before returning records. A frontend must not
fetch the owner's complete history and slice it in the browser. Previous/Next controls
expose the visible range and page count in a polite live region and use native buttons so
keyboard and assistive-technology behavior remains predictable.

[`pagination-inventory.json`](pagination-inventory.json) classifies every currently
discovered list endpoint, table-rendering source file, and known growing card/list
surface. `uv run python scripts/check_pagination_inventory.py` fails when a new list
route or table appears without a classification, when a pending surface lacks its Beads
issue, or when a stale classification remains. `pending` entries are required work for
`hc-inbox.10`; they are not accepted as complete pagination.
