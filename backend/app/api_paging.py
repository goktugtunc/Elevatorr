"""`PageDep`: limit/offset query parameters as a FastAPI dependency.

FastAPI 0.141 (pinned) does not expand `page: Annotated[PageParams, Query()]` into query fields — it
reports the whole model as a missing `page` parameter — so paginated endpoints take `page: PageDep`
instead, which yields the same `app.schemas.common.PageParams` object.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query

from app.schemas.common import PageParams


def page_params(
    limit: Annotated[int, Query(ge=1, le=100, description="page size")] = 20,
    offset: Annotated[int, Query(ge=0, description="rows to skip")] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]

__all__ = ["PageDep", "page_params"]
