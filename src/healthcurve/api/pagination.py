"""Shared bounded pagination contract for owner-scoped record collections."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.api.schemas import PageMetadata
from healthcurve.events.base import EventMixin

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


@dataclass(frozen=True, slots=True)
class CurrentFactPage[E: EventMixin]:
    items: list[E]
    revisions: list[E]
    metadata: PageMetadata


def paginate_current_facts[E: EventMixin](
    session: Session,
    model: type[E],
    *,
    owner_id: uuid.UUID,
    request: PageRequest,
    predicates: tuple[ColumnElement[bool], ...] = (),
) -> CurrentFactPage[E]:
    """Page current facts and fetch correction ancestors only for visible rows."""
    current = select(model).where(
        model.owner_id == owner_id,
        model.id.not_in(
            select(model.supersedes_id).where(
                model.owner_id == owner_id,
                model.supersedes_id.is_not(None),
            )
        ),
        *predicates,
    )
    total_items = session.scalar(select(func.count()).select_from(current.subquery())) or 0
    metadata = page_metadata(total_items, request)
    items = list(
        session.scalars(
            current.order_by(model.occurred_at.desc(), model.id.asc())
            .offset(request.offset)
            .limit(request.page_size)
        )
    )

    revisions: list[E] = []
    visited: set[uuid.UUID] = {row.id for row in items}
    pending = {row.supersedes_id for row in items if row.supersedes_id is not None}
    while pending:
        prior_rows = list(
            session.scalars(select(model).where(model.owner_id == owner_id, model.id.in_(pending)))
        )
        revisions.extend(prior_rows)
        visited.update(row.id for row in prior_rows)
        pending = {
            row.supersedes_id
            for row in prior_rows
            if row.supersedes_id is not None and row.supersedes_id not in visited
        }
    return CurrentFactPage(items=items, revisions=revisions, metadata=metadata)
