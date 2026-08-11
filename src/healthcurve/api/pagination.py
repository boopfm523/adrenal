"""Shared bounded pagination contract for owner-scoped record collections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status

from healthcurve.api.schemas import PageMetadata

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageRequest:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_request(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PageRequest:
    """Validate the common public query parameters before a collection is queried."""
    return PageRequest(page=page, page_size=page_size)


Pagination = Annotated[PageRequest, Depends(page_request)]


def page_metadata(total_items: int, request: PageRequest) -> PageMetadata:
    """Describe a page and reject pages beyond the collection's last page."""
    total_pages = max(1, (total_items + request.page_size - 1) // request.page_size)
    if request.page > total_pages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "page_out_of_range",
                "page": request.page,
                "total_pages": total_pages,
            },
        )
    return PageMetadata(
        page=request.page,
        page_size=request.page_size,
        total_items=total_items,
        total_pages=total_pages,
    )
