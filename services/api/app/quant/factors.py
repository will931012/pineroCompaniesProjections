"""Factor scores: cross-sectional composites over the model universe on each date.

For each input: winsorise at the 1st and 99th percentiles across companies, convert to a
z-score, and flip the sign where lower is better (leverage, volatility, beta). A factor is the
average of its available input z-scores (at least half must be present); its percentile is the
company's rank among all scored companies that date. Scores describe; they are not forecasts.
"""

import math
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import FactorScore, FeatureSnapshot
from app.quant.features import FEATURE_SET_VERSION

FACTOR_VERSION = "fs1"
# factor -> [(feature, sign)]
FACTORS: dict[str, list[tuple[str, int]]] = {
    "value": [
        ("earnings_yield", 1), ("book_to_price", 1), ("ebitda_to_ev", 1), ("fcf_yield", 1),
        ("sales_yield", 1),
    ],
    "quality": [("roe", 1), ("roic", 1), ("gross_margin", 1), ("debt_to_equity", -1)],
    "momentum": [("mom_12_1", 1), ("mom_6m", 1)],
    "low_volatility": [("vol_3m", -1), ("beta_1y", -1)],
    "growth": [("revenue_growth_yoy", 1), ("revenue_cagr_3y", 1), ("eps_growth_yoy", 1)],
    "size": [("log_market_cap", 1)],
}  # fmt: skip
LABELS = {
    "value": "Value", "quality": "Quality", "momentum": "Momentum",
    "low_volatility": "Low volatility", "growth": "Growth", "size": "Size",
}  # fmt: skip


def zscores(values: list[float | None]) -> list[float | None]:
    present = np.array([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if len(present) < 10:
        return [None] * len(values)
    low, high = np.percentile(present, [1, 99])
    clipped = np.clip(present, low, high)
    mean, std = float(clipped.mean()), float(clipped.std())
    if std <= 0:
        return [None] * len(values)
    return [
        (min(max(v, low), high) - mean) / std if v is not None and math.isfinite(v) else None
        for v in values
    ]


def score_date(
    company_ids: list[int], features: list[dict[str, Any]]
) -> list[tuple[int, str, float, float, dict[str, Any]]]:
    """(company, factor, score, percentile, inputs) for one date."""
    names = {name for inputs in FACTORS.values() for name, _ in inputs}
    z = {name: zscores([f.get(name) for f in features]) for name in names}
    out = []
    for factor, inputs in FACTORS.items():
        scores: list[float | None] = []
        details: list[dict[str, Any]] = []
        for position in range(len(company_ids)):
            parts: list[tuple[str, int, float]] = [
                (name, sign, value)
                for name, sign in inputs
                if (value := z[name][position]) is not None
            ]
            if len(parts) * 2 < len(inputs):
                scores.append(None)
                details.append({})
                continue
            scores.append(sum(sign * value for _, sign, value in parts) / len(parts))
            details.append(
                {
                    name: {"value": features[position].get(name), "z": value}
                    for name, _, value in parts
                }
            )
        valid = sorted(s for s in scores if s is not None)
        for position, s in enumerate(scores):
            if s is None:
                continue
            percentile = (sum(1 for v in valid if v <= s)) / len(valid)
            out.append((company_ids[position], factor, s, percentile, details[position]))
    return out


def compute_factor_scores(db: Session, dates: list[date]) -> int:
    rows = db.execute(
        select(FeatureSnapshot.as_of, FeatureSnapshot.company_id, FeatureSnapshot.features)
        .where(FeatureSnapshot.version == FEATURE_SET_VERSION, FeatureSnapshot.as_of.in_(dates))
        .order_by(FeatureSnapshot.as_of, FeatureSnapshot.company_id)
    ).all()
    by_date: dict[date, list[Any]] = {}
    for r in rows:
        by_date.setdefault(r.as_of, []).append(r)
    db.execute(
        delete(FactorScore).where(
            FactorScore.version == FACTOR_VERSION, FactorScore.as_of.in_(dates)
        )
    )
    stored = 0
    for day, items in by_date.items():
        scored = score_date([r.company_id for r in items], [r.features for r in items])
        values = [
            {
                "version": FACTOR_VERSION, "as_of": day, "company_id": company, "factor": factor,
                "score": score, "percentile": percentile, "inputs": inputs,
            }
            for company, factor, score, percentile, inputs in scored
        ]  # fmt: skip
        for i in range(0, len(values), 3000):
            db.execute(insert(FactorScore).values(values[i : i + 3000]))
        stored += len(values)
    db.commit()
    return stored
