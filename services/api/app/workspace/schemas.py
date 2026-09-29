import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WatchlistItemOut(BaseModel):
    ticker: str
    name: str
    exchange: str | None
    is_active: bool
    added_at: datetime


class WatchlistOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    items: list[WatchlistItemOut]


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WatchlistItemCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
