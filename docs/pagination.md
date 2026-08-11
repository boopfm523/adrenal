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
discovered list endpoint, table-rendering source file, known growing collection, and
file that maps records into article cards. Each entry records its pagination or bounded
contract, meaningful date-filter category, health-data sensitivity, and exception
rationale. Paginated frontend histories must declare semantic-table rendering,
responsive horizontal containment, and URL-backed page/filter state. Local-date API
contracts must declare an explicit IANA timezone.

`uv run python scripts/check_pagination_inventory.py` fails when a new list route,
table, or mapped-card file appears without classification; a discovered entry becomes
stale; a paginated surface omits its Beads issue or contract; or a bounded reference
lacks a rationale. Mapped articles are allowed only for bounded child content such as
revision details on the current page, never as an unbounded primary history. Static
forms, fixed configuration children, and accessible chart fallback tables may be
excluded only through a recorded bounded-reference rationale. `pending` is reserved for
linked follow-up work and is not evidence of completed pagination.

Chart exact-value tables inherit a documented bound from their data source. Vitals and
lab charts use only the current visible API page. Analytics charts use the selected date
range, which the API caps at 366 days. They never fetch a separate unbounded history for
the table alternative.
