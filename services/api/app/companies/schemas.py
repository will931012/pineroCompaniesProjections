from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SourceRef(BaseModel):
    """Where a set of values came from. Every externally sourced field links to one."""

    fetch_id: int
    provider: str
    dataset: str
    source_url: str
    retrieved_at: datetime
    license_note: str | None


class CompanySummary(BaseModel):
    ticker: str
    name: str
    exchange: str | None
    cik: int | None
    sector: str | None
    industry: str | None
    country: str | None
    is_active: bool


class CompanySearchResponse(BaseModel):
    items: list[CompanySummary]
    total: int


class ListingOut(BaseModel):
    ticker: str
    exchange: str | None
    is_active: bool
    first_seen_at: datetime | None
    last_seen_at: datetime | None


class FormerName(BaseModel):
    name: str
    date_from: str | None
    date_to: str | None


class Classification(BaseModel):
    system: Literal["SIC"]
    code: str
    sector: str | None
    industry: str | None


ProfileStatus = Literal["current", "stale", "unavailable", "not_configured", "not_applicable"]


class DataAvailability(BaseModel):
    status: Literal["available", "not_configured", "planned"]
    detail: str


class CompanyProfile(BaseModel):
    ticker: str
    name: str
    exchange: str | None
    cik: int | None
    country: str | None
    classification: Classification | None
    entity_type: str | None
    filer_category: str | None
    state_of_incorporation: str | None
    fiscal_year_end: str | None
    website: str | None
    description: str | None
    headquarters: str | None
    former_names: list[FormerName]
    listings: list[ListingOut]
    profile_status: ProfileStatus
    profile_message: str | None
    sources: list[SourceRef]
    availability: dict[str, DataAvailability]
