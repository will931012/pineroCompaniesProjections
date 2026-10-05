import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

Factor = Literal["value", "quality", "momentum", "low_volatility", "growth", "size"]


class FactorWeight(BaseModel):
    factor: Factor
    weight: Annotated[float, Field(gt=0, le=1)]


class CostsIn(BaseModel):
    commission_per_share: Annotated[float, Field(ge=0, le=0.05)] = 0.005
    min_commission: Annotated[float, Field(ge=0, le=50)] = 1.0
    spread_fallback: Annotated[float, Field(ge=0, le=0.05)] = 0.0010
    spread_cap: Annotated[float, Field(ge=0, le=0.2)] = 0.02
    impact_coefficient: Annotated[float, Field(ge=0, le=5)] = 0.5
    max_participation: Annotated[float, Field(gt=0, le=1)] = 0.10


class BacktestIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    factors: Annotated[list[FactorWeight], Field(min_length=1, max_length=6)]
    selection: Literal["top_n", "top_quantile"] = "top_n"
    top_n: Annotated[int, Field(ge=5, le=100)] = 20
    top_quantile: Annotated[float, Field(ge=0.05, le=0.5)] = 0.2
    weighting: Literal["equal", "score", "inverse_volatility"] = "equal"
    max_weight: Annotated[float, Field(ge=0.01, le=1)] = 0.10
    rebalance: Literal["monthly", "quarterly"] = "monthly"
    start: date | None = None
    end: date | None = None
    capital: Annotated[float, Field(ge=10_000, le=10_000_000_000)] = 1_000_000
    costs: CostsIn = Field(default_factory=CostsIn)

    @model_validator(mode="after")
    def _check(self) -> "BacktestIn":
        if len({f.factor for f in self.factors}) != len(self.factors):
            raise ValueError("Each factor may appear once.")
        if self.start and self.end and self.start >= self.end:
            raise ValueError("The start date must be before the end date.")
        return self


class FactorOption(BaseModel):
    key: str
    label: str
    inputs: list[str]


class CostDefault(BaseModel):
    key: str
    label: str
    value: float
    explanation: str


class OptionsOut(BaseModel):
    factors: list[FactorOption]
    first_signal: date | None
    last_signal: date | None
    universe_size: int
    costs: list[CostDefault]
    notes: list[str]


class BacktestSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    summary: dict[str, Any]
    spec: dict[str, Any]


class BacktestOut(BacktestSummary):
    results: dict[str, Any]
    error: str | None
    code_version: str
    data_through: date | None
    duration_ms: int
