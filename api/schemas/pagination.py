from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    sort_by: str = ""
    sort_direction: str = "desc"
