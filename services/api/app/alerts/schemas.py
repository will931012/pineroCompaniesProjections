import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.events.taxonomy import EVENT_TYPES

RuleKind = Literal["filing", "earnings", "news", "insider", "price"]
TRANSACTION_CODES = {"P", "S", "A", "M", "X", "C", "F", "G", "D", "J"}
ALERT_FORMS = {"10-K", "10-Q", "8-K", "S-1", "S-3", "S-4", "DEF 14A", "SC 13D", "SC 13G", "4"}


class RuleParams(BaseModel):
    forms: list[str] | None = Field(default=None, max_length=10)
    event_types: list[str] | None = Field(default=None, max_length=len(EVENT_TYPES))
    first_only: bool = True
    codes: list[str] | None = Field(default=None, max_length=10)
    min_value: float | None = Field(default=None, ge=0, le=1e12)
    min_move_pct: float | None = Field(default=None, gt=0, le=100)

    @field_validator("forms")
    @classmethod
    def _forms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        forms = [f.strip().upper() for f in value]
        unknown = sorted(set(forms) - ALERT_FORMS)
        if unknown:
            raise ValueError(f"Unsupported forms: {', '.join(unknown)}.")
        return forms

    @field_validator("event_types")
    @classmethod
    def _types(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (unknown := sorted(set(value) - set(EVENT_TYPES))):
            raise ValueError(f"Unknown event types: {', '.join(unknown)}.")
        return value

    @field_validator("codes")
    @classmethod
    def _codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        codes = [c.strip().upper() for c in value]
        if unknown := sorted(set(codes) - TRANSACTION_CODES):
            raise ValueError(f"Unknown Form 4 codes: {', '.join(unknown)}.")
        return codes


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: RuleKind
    params: RuleParams = Field(default_factory=RuleParams)
    tickers: list[str] = Field(default_factory=list, max_length=50)
    watchlist_id: uuid.UUID | None = None
    email: bool = True
    active: bool = True

    @model_validator(mode="after")
    def _scope(self) -> "RuleIn":
        self.tickers = list(dict.fromkeys(t.strip().upper() for t in self.tickers if t.strip()))
        if not self.tickers and self.watchlist_id is None:
            raise ValueError("Choose at least one ticker or a watchlist.")
        return self


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    params: RuleParams | None = None
    tickers: list[str] | None = Field(default=None, max_length=50)
    watchlist_id: uuid.UUID | None = None
    email: bool | None = None
    active: bool | None = None


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: RuleKind
    params: dict[str, object]
    tickers: list[str]
    watchlist_id: uuid.UUID | None
    watchlist_name: str | None
    email: bool
    active: bool
    created_at: datetime
    evaluated_at: datetime | None
    # Companies currently in scope (tickers + watchlist).
    companies: int


class AlertOut(BaseModel):
    id: int
    rule_id: uuid.UUID
    rule_name: str
    title: str
    body: str
    link: str | None
    occurred_at: datetime
    created_at: datetime
    email_status: Literal["pending", "sent", "failed", "not_configured", "disabled"]
    email_error: str | None
    read: bool


class AlertsResponse(BaseModel):
    alerts: list[AlertOut]
    unread: int


class JobRunOut(BaseModel):
    kind: str
    status: str
    finished_at: datetime | None
    result: dict[str, object] | None
    last_error: str | None


class AlertStatus(BaseModel):
    email_configured: bool
    email_provider: str | None
    email_from: str
    recipient: str
    # Latest run of each background job the alerts depend on (worker health).
    jobs: list[JobRunOut]


class ReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=500)


class EvaluateOut(BaseModel):
    created: int
    sent: int
    failed: int
