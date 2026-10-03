"""Prediction journal: every served prediction is stored once and scored when its horizon ends.

Predictions are model output. Each row keeps the model version, the date of the newest input
it used, the calibrated probability, the 10th–90th percentile range of the excess return, and
the five largest SHAP contributions (in log-odds of the raw model), so the reasons behind a
number stay inspectable after the model is retrained.
"""

import math
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ModelVersion, Prediction, PredictionOutcome, Security, UniverseMember
from app.quant.features import CROSS_SECTIONAL
from app.quant.modeling import (
    QUANTILES,
    Dataset,
    assemble,
    calibrator_of,
    forward_excess,
    load_boosters,
)
from app.quant.prices import ReturnSeries, return_series
from app.quant.universe import UNIVERSE_VERSION

TOP_DRIVERS = 5


def active_versions(db: Session) -> list[ModelVersion]:
    return list(
        db.scalars(
            select(ModelVersion)
            .where(ModelVersion.status == "active")
            .order_by(ModelVersion.horizon_days)
        )
    )


def _finite(value: float) -> float | None:
    """JSON (and PostgreSQL JSONB) has no NaN: a missing input is null."""
    return float(value) if math.isfinite(value) else None


def _drivers(contributions: np.ndarray, row: int, ds: Dataset, index: int) -> list[dict[str, Any]]:
    values = contributions[row, :-1]
    order = np.argsort(-np.abs(values))[:TOP_DRIVERS]
    out = []
    for j in order:
        name = ds.feature_names[j]
        out.append(
            {
                "feature": name,
                "value": ds.raw[index].get(name),
                "percentile": _finite(ds.X[index, j]) if name in CROSS_SECTIONAL else None,
                "contribution": float(values[j]),
            }
        )
    return out


def predict_latest(db: Session, benchmark: ReturnSeries) -> dict[str, int]:
    """Predict for every company in the newest feature snapshot, once per active model."""
    stored: dict[str, int] = {}
    for version in active_versions(db):
        ds = assemble(db, version.horizon_days, benchmark)
        if not ds.as_of:
            continue
        day = max(ds.as_of)
        rows = np.array([i for i, d in enumerate(ds.as_of) if d == day])
        boosters = load_boosters(version)
        X = ds.X[rows]
        raw = np.asarray(boosters["direction"].predict(X))
        calibrated = calibrator_of(version).apply(raw)
        contributions = np.asarray(boosters["direction"].predict(X, pred_contrib=True))
        quantiles = {a: np.asarray(boosters[f"q{a}"].predict(X)) for a in QUANTILES}
        records = []
        for position, index in enumerate(rows):
            low, mid, high = sorted(float(quantiles[a][position]) for a in QUANTILES)
            records.append(
                {
                    "model_version_id": version.id,
                    "company_id": ds.company_ids[index],
                    "as_of": day,
                    "horizon_days": version.horizon_days,
                    "probability_raw": float(raw[position]),
                    "probability": float(calibrated[position]),
                    "expected_excess": mid,
                    "excess_low": low,
                    "excess_high": high,
                    "drivers": _drivers(contributions, position, ds, int(index)),
                    "data_available_on": ds.data_available[index],
                }
            )
        if records:
            db.execute(
                insert(Prediction)
                .values(records)
                .on_conflict_do_nothing(index_elements=["model_version_id", "company_id", "as_of"])
            )
            db.commit()
        stored[version.name] = len(records)
    return stored


def score_outcomes(db: Session, benchmark: ReturnSeries) -> int:
    """Record realised excess returns for predictions whose horizon has passed."""
    pending = (
        db.execute(
            select(Prediction)
            .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
            .where(PredictionOutcome.prediction_id.is_(None))
        )
        .scalars()
        .all()
    )
    listings = dict(
        db.execute(
            select(UniverseMember.company_id, Security.id)
            .join(Security, Security.id == UniverseMember.security_id)
            .where(UniverseMember.version == UNIVERSE_VERSION)
            .distinct()
        ).all()
    )
    cache: dict[int, ReturnSeries] = {}
    scored = 0
    for prediction in pending:
        security = listings.get(prediction.company_id)
        if security is None:
            continue
        if security not in cache:
            cache[security] = return_series(db, security)
        result = forward_excess(
            cache[security], benchmark, prediction.as_of, prediction.horizon_days
        )
        if result is None:
            continue
        excess, end = result
        went_up = excess > 0
        db.add(
            PredictionOutcome(
                prediction_id=prediction.id,
                end_date=end,
                excess_return=excess,
                went_up=went_up,
                brier=(prediction.probability - float(went_up)) ** 2,
            )
        )
        scored += 1
    db.commit()
    return scored


def journal_summary(db: Session, version: ModelVersion) -> dict[str, Any]:
    """Live (post-training) track record of a model version."""
    rows = db.execute(
        select(Prediction.probability, PredictionOutcome.went_up, PredictionOutcome.excess_return)
        .join(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(Prediction.model_version_id == version.id)
    ).all()
    total = db.scalar(
        select(Prediction.id).where(Prediction.model_version_id == version.id).limit(1)
    )
    if not rows:
        return {"scored": 0, "has_predictions": total is not None}
    p = np.array([r[0] for r in rows])
    up = np.array([float(r[1]) for r in rows])
    return {
        "scored": len(rows),
        "has_predictions": True,
        "brier": float(np.mean((p - up) ** 2)),
        "hit_rate": float(np.mean((p > 0.5) == (up > 0.5))),
        "mean_probability": float(p.mean()),
        "observed_rate": float(up.mean()),
    }


def latest_for_company(db: Session, company_id: int) -> list[tuple[Prediction, ModelVersion]]:
    out = []
    for version in active_versions(db):
        prediction = db.scalar(
            select(Prediction)
            .where(Prediction.model_version_id == version.id, Prediction.company_id == company_id)
            .order_by(Prediction.as_of.desc())
            .limit(1)
        )
        if prediction:
            out.append((prediction, version))
    return out


def history_for_company(db: Session, company_id: int, limit: int = 36) -> list[Any]:
    return list(
        db.execute(
            select(Prediction, PredictionOutcome, ModelVersion.name)
            .join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
            .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
            .where(Prediction.company_id == company_id)
            .order_by(Prediction.as_of.desc())
            .limit(limit)
        ).all()
    )
