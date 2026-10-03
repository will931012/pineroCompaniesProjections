"""Return models: targets, dataset, walk-forward evaluation, calibration, and SHAP.

Target: excess total return over the S&P 500 (SPY) across the next H trading days (H = 21,
about a month, and 63, about a quarter), measured from the as-of close. Direction models
predict P(excess > 0); quantile models give the 10th/50th/90th percentile of the excess return.

Inputs are the stored feature snapshots. Company-level features enter as cross-sectional
percentile ranks on each date (so a model compares companies with each other rather than
learning the level of, say, interest rates through P/E); market-wide features enter raw.

Walk-forward evaluation, one fold per calendar year: train on every row whose label was
known at least EMBARGO_DAYS before the year starts (purged by label end date, never shuffled),
hold out the last CALIBRATION_MONTHS of that training window to calibrate, and test on the
year. The baseline is a regularised logistic regression on the same inputs.
"""

import bisect
import json
import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
from scipy.stats import rankdata, spearmanr
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Company, FeatureSnapshot, MarketRegime, ModelVersion, UniverseMember
from app.quant.features import CROSS_SECTIONAL, FEATURE_SET_VERSION
from app.quant.prices import ReturnSeries, return_series
from app.quant.regime import REGIME_VERSION
from app.quant.universe import UNIVERSE_VERSION

logger = logging.getLogger(__name__)

CODE_VERSION = "m2"
# Models rank companies, so they see only company-level inputs (as cross-sectional ranks).
# Market-wide inputs (macro, S&P 500 trend) are identical for every company on a date: in
# version m1 they let the model learn per-date levels and extrapolate them (out-of-sample
# Brier worse than the base rate), so from m2 they are used for regimes and segments only.
MODEL_FEATURES = CROSS_SECTIONAL
HORIZONS = (21, 63)
EMBARGO_DAYS = 31
CALIBRATION_MONTHS = 12
MIN_TRAIN_YEARS = 3
QUANTILES = (0.1, 0.5, 0.9)
ROUNDS = 300
LGB_PARAMS: dict[str, Any] = {
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 7,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbose": -1,
}

Matrix = npt.NDArray[np.float64]


# --- Targets ------------------------------------------------------------------------------


def forward_excess(
    series: ReturnSeries, benchmark: ReturnSeries, day: date, horizon: int
) -> tuple[float, date] | None:
    """Excess total return from the last close on or before `day` over `horizon` bars."""
    i = bisect.bisect_right(series.dates, day) - 1
    if i < 0 or i + horizon >= len(series.dates) or (day - series.dates[i]).days > 7:
        return None
    end = series.dates[i + horizon]
    index = series.index
    own = index[i + horizon] / index[i] - 1
    j = bisect.bisect_right(benchmark.dates, day) - 1
    k = bisect.bisect_right(benchmark.dates, end) - 1
    if j < 0 or k <= j:
        return None
    bench_index = benchmark.index
    return own - (bench_index[k] / bench_index[j] - 1), end


# --- Dataset ------------------------------------------------------------------------------


@dataclass
class Dataset:
    horizon: int
    feature_names: list[str]
    as_of: list[date]
    company_ids: list[int]
    X: Matrix
    raw: list[dict[str, float | None]]
    excess: Matrix  # NaN when the label is not known yet
    label_end: list[date | None]
    data_available: list[date]
    sector: list[str]
    regime: list[str]

    def rows(self, mask: npt.NDArray[np.bool_]) -> npt.NDArray[np.intp]:
        return np.flatnonzero(mask)


def _cross_sectional_ranks(values: list[float | None]) -> list[float]:
    """Percentile ranks in (0, 1] among the non-missing values; NaN where missing."""
    present = [i for i, v in enumerate(values) if v is not None]
    out = [math.nan] * len(values)
    if not present:
        return out
    ranks = rankdata([values[i] for i in present], method="average")
    for position, i in enumerate(present):
        out[i] = float(ranks[position]) / len(present)
    return out


def assemble(db: Session, horizon: int, benchmark: ReturnSeries) -> Dataset:
    snapshots = db.execute(
        select(
            FeatureSnapshot.as_of, FeatureSnapshot.company_id, FeatureSnapshot.features,
            FeatureSnapshot.data_available_on,
        )
        .where(FeatureSnapshot.version == FEATURE_SET_VERSION)
        .order_by(FeatureSnapshot.as_of, FeatureSnapshot.company_id)
    ).all()  # fmt: skip
    listings = dict(
        db.execute(
            select(UniverseMember.company_id, UniverseMember.security_id)
            .where(
                UniverseMember.version == UNIVERSE_VERSION, UniverseMember.security_id.is_not(None)
            )
            .distinct()
        ).all()
    )
    sectors = dict(db.execute(select(Company.id, Company.sector)).all())
    regimes = dict(
        db.execute(
            select(MarketRegime.as_of, MarketRegime.label).where(
                MarketRegime.version == REGIME_VERSION
            )
        ).all()
    )
    series_cache: dict[int, ReturnSeries] = {}

    def series_of(company_id: int) -> ReturnSeries | None:
        security = listings.get(company_id)
        if security is None:
            return None
        if security not in series_cache:
            series_cache[security] = return_series(db, security)
        return series_cache[security]

    by_date: dict[date, list[Any]] = defaultdict(list)
    for row in snapshots:
        by_date[row.as_of].append(row)

    as_of, companies, raw, excess, ends, available, sector, regime = [], [], [], [], [], [], [], []
    matrix_rows: list[list[float]] = []
    for day in sorted(by_date):
        rows = by_date[day]
        ranks = {
            name: _cross_sectional_ranks([r.features.get(name) for r in rows])
            for name in CROSS_SECTIONAL
        }
        for position, r in enumerate(rows):
            vector = [
                ranks[name][position]
                if name in ranks
                else (r.features.get(name) if r.features.get(name) is not None else math.nan)
                for name in MODEL_FEATURES
            ]
            series = series_of(r.company_id)
            label = forward_excess(series, benchmark, day, horizon) if series else None
            matrix_rows.append([float(v) for v in vector])
            as_of.append(day)
            companies.append(r.company_id)
            raw.append(r.features)
            excess.append(label[0] if label else math.nan)
            ends.append(label[1] if label else None)
            available.append(r.data_available_on)
            sector.append(sectors.get(r.company_id) or "Unclassified")
            regime.append(regimes.get(day, "Unknown"))
    return Dataset(
        horizon, list(MODEL_FEATURES), as_of, companies,
        np.array(matrix_rows, dtype=np.float64).reshape(len(matrix_rows), len(MODEL_FEATURES)),
        raw, np.array(excess, dtype=np.float64), ends, available, sector, regime,
    )  # fmt: skip


# --- Models -------------------------------------------------------------------------------


def fit_direction(X: Matrix, up: Matrix) -> lgb.Booster:
    data = lgb.Dataset(X, label=up, free_raw_data=False)
    return lgb.train({**LGB_PARAMS, "objective": "binary"}, data, num_boost_round=ROUNDS)


def fit_quantiles(X: Matrix, excess: Matrix) -> dict[float, lgb.Booster]:
    return {
        alpha: lgb.train(
            {**LGB_PARAMS, "objective": "quantile", "alpha": alpha},
            lgb.Dataset(X, label=excess, free_raw_data=False),
            num_boost_round=ROUNDS,
        )
        for alpha in QUANTILES
    }


@dataclass
class Baseline:
    fill: Matrix
    mean: Matrix
    scale: Matrix
    model: LogisticRegression

    def predict(self, X: Matrix) -> Matrix:
        Z = np.where(np.isnan(X), self.fill, X)
        return np.asarray(self.model.predict_proba((Z - self.mean) / self.scale)[:, 1])


def fit_baseline(X: Matrix, up: Matrix) -> Baseline:
    fill = np.nanmedian(X, axis=0)
    fill = np.where(np.isnan(fill), 0.5, fill)
    Z = np.where(np.isnan(X), fill, X)
    mean, scale = Z.mean(axis=0), Z.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    model = LogisticRegression(C=0.1, max_iter=2000)
    model.fit((Z - mean) / scale, up)
    return Baseline(fill, mean, scale, model)


@dataclass
class Calibrator:
    method: str  # platt | isotonic | none
    params: dict[str, Any] = field(default_factory=dict)

    def apply(self, p: Matrix) -> Matrix:
        if self.method == "platt":
            logit = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
            return 1 / (1 + np.exp(-(self.params["a"] * logit + self.params["b"])))
        if self.method == "isotonic":
            return np.interp(p, self.params["x"], self.params["y"])
        return p


def _fit_platt(p: Matrix, y: Matrix) -> Calibrator:
    logit = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    model = LogisticRegression(C=1e6, max_iter=1000)
    model.fit(logit.reshape(-1, 1), y)
    return Calibrator("platt", {"a": float(model.coef_[0][0]), "b": float(model.intercept_[0])})


def _fit_isotonic(p: Matrix, y: Matrix) -> Calibrator:
    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(p, y)
    return Calibrator(
        "isotonic",
        {
            "x": [float(v) for v in model.X_thresholds_],
            "y": [float(v) for v in model.y_thresholds_],
        },
    )


def choose_calibrator(p: Matrix, y: Matrix, as_of: list[date]) -> tuple[Calibrator, dict[str, Any]]:
    """Fit Platt and isotonic on the first half of the window, score both on the second half
    (time-separated), then refit the better one on the whole window."""
    if len(p) < 200 or len(set(y.tolist())) < 2:
        return Calibrator("none"), {"reason": "too few calibration rows"}
    order = np.argsort(np.array([d.toordinal() for d in as_of]), kind="stable")
    half = len(order) // 2
    first, second = order[:half], order[half:]
    scores = {"none": float(brier_score_loss(y[second], p[second]))}
    for name, fit in (("platt", _fit_platt), ("isotonic", _fit_isotonic)):
        if len(set(y[first].tolist())) < 2:
            continue
        scores[name] = float(brier_score_loss(y[second], fit(p[first], y[first]).apply(p[second])))
    best = min(scores, key=lambda k: scores[k])
    chosen = {"platt": _fit_platt, "isotonic": _fit_isotonic}.get(best)
    return (chosen(p, y) if chosen else Calibrator("none")), {
        "brier_by_method": scores,
        "chosen": best,
    }


# --- Evaluation ---------------------------------------------------------------------------


def calibration_bins(p: Matrix, y: Matrix, bins: int = 10) -> list[dict[str, float | int]]:
    out = []
    edges = np.linspace(0, 1, bins + 1)
    for low, high in pairwise(edges):
        mask = (p >= low) & ((p < high) if high < 1 else (p <= high))
        if mask.sum() == 0:
            continue
        out.append(
            {
                "low": float(low), "high": float(high), "count": int(mask.sum()),
                "predicted": float(p[mask].mean()), "observed": float(y[mask].mean()),
            }
        )  # fmt: skip
    return out


def rank_ic(as_of: list[date], score: Matrix, excess: Matrix) -> dict[str, Any]:
    """Spearman correlation of score and realised excess return, per date."""
    by_date: dict[date, list[int]] = defaultdict(list)
    for i, d in enumerate(as_of):
        by_date[d].append(i)
    series = []
    for d in sorted(by_date):
        idx = by_date[d]
        if len(idx) < 20:
            continue
        rho = spearmanr(score[idx], excess[idx]).statistic
        if rho is not None and not math.isnan(rho):
            series.append((d, float(rho)))
    values = [v for _, v in series]
    if len(values) < 2:
        return {"dates": len(values), "mean": None, "std": None, "t_stat": None, "series": []}
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    return {
        "dates": len(values),
        "mean": mean,
        "std": std,
        "t_stat": mean / std * math.sqrt(len(values)) if std > 0 else None,
        "positive_share": sum(v > 0 for v in values) / len(values),
        "series": [(d.isoformat(), v) for d, v in series],
    }


def decile_spread(as_of: list[date], score: Matrix, excess: Matrix) -> float | None:
    """Average over dates of (top-decile mean excess − bottom-decile mean excess)."""
    by_date: dict[date, list[int]] = defaultdict(list)
    for i, d in enumerate(as_of):
        by_date[d].append(i)
    spreads = []
    for idx in by_date.values():
        if len(idx) < 30:
            continue
        ordered = sorted(idx, key=lambda i: score[i])
        n = max(1, len(ordered) // 10)
        spreads.append(float(np.mean(excess[ordered[-n:]]) - np.mean(excess[ordered[:n]])))
    return sum(spreads) / len(spreads) if spreads else None


def classification_metrics(as_of: list[date], p: Matrix, excess: Matrix) -> dict[str, Any]:
    up = (excess > 0).astype(float)
    metrics: dict[str, Any] = {
        "rows": len(p),
        "base_rate": float(up.mean()) if len(up) else None,
        "brier": float(brier_score_loss(up, p)) if len(p) else None,
        "brier_base_rate": float(brier_score_loss(up, np.full(len(up), up.mean())))
        if len(up)
        else None,
        "log_loss": float(log_loss(up, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1]))
        if len(p)
        else None,
        "auc": float(roc_auc_score(up, p)) if len(set(up.tolist())) == 2 else None,
        "hit_rate": float(((p > 0.5) == (up > 0.5)).mean()) if len(p) else None,
    }
    metrics["rank_ic"] = rank_ic(as_of, p, excess)
    metrics["decile_spread"] = decile_spread(as_of, p, excess)
    return metrics


def segment_metrics(
    keys: list[str], as_of: list[date], p: Matrix, excess: Matrix, min_rows: int = 300
) -> dict[str, Any]:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        groups[k].append(i)
    out = {}
    for key, idx in sorted(groups.items()):
        if len(idx) < min_rows:
            continue
        up = (excess[idx] > 0).astype(float)
        out[key] = {
            "rows": len(idx),
            "auc": float(roc_auc_score(up, p[idx])) if len(set(up.tolist())) == 2 else None,
            "brier": float(brier_score_loss(up, p[idx])),
            "rank_ic_mean": rank_ic([as_of[i] for i in idx], p[idx], excess[idx])["mean"],
        }
    return out


def quantile_metrics(excess: Matrix, q: dict[float, Matrix]) -> dict[str, Any]:
    low, mid, high = q[0.1], q[0.5], q[0.9]
    pinball = float(np.mean(np.maximum(0.5 * (excess - mid), -0.5 * (excess - mid))))
    return {
        "coverage_10_90": float(((excess >= low) & (excess <= high)).mean()),
        "pinball_median": pinball,
        "median_abs_error": float(np.mean(np.abs(excess - mid))),
    }


# --- Training -----------------------------------------------------------------------------


@dataclass
class FoldResult:
    year: int
    train_rows: int
    calibration_rows: int
    test_rows: int
    calibrator: dict[str, Any]
    model: dict[str, Any]
    baseline: dict[str, Any]


def _known_before(ds: Dataset, cutoff: date) -> npt.NDArray[np.bool_]:
    return np.array([e is not None and e < cutoff for e in ds.label_end])


def _fit_window(
    ds: Dataset, train: npt.NDArray[np.intp]
) -> tuple[lgb.Booster, Calibrator, dict[str, Any], dict[float, lgb.Booster], Baseline, int]:
    """Fit on `train` minus its last CALIBRATION_MONTHS (purged), calibrate on those months."""
    last = max(ds.as_of[i] for i in train)
    calibration_start = last - timedelta(days=CALIBRATION_MONTHS * 31)
    core = np.array(
        [i for i in train if ds.label_end[i] is not None
         and ds.label_end[i] < calibration_start - timedelta(days=EMBARGO_DAYS)]
    )  # fmt: skip
    calibration = np.array([i for i in train if ds.as_of[i] >= calibration_start])
    up = (ds.excess > 0).astype(float)
    booster = fit_direction(ds.X[core], up[core])
    raw = booster.predict(ds.X[calibration])
    calibrator, info = choose_calibrator(
        np.asarray(raw), up[calibration], [ds.as_of[i] for i in calibration]
    )
    quantiles = fit_quantiles(ds.X[train], ds.excess[train])
    baseline = fit_baseline(ds.X[train], up[train])
    return booster, calibrator, info, quantiles, baseline, len(calibration)


def walk_forward(ds: Dataset) -> tuple[list[FoldResult], dict[str, Any]]:
    labelled = ~np.isnan(ds.excess)
    years = sorted({d.year for d, ok in zip(ds.as_of, labelled, strict=True) if ok})
    if not years:
        return [], {}
    folds: list[FoldResult] = []
    pooled: dict[str, list[Any]] = defaultdict(list)
    for year in years[MIN_TRAIN_YEARS:]:
        start = date(year, 1, 1)
        train = np.flatnonzero(_known_before(ds, start - timedelta(days=EMBARGO_DAYS)))
        test = np.flatnonzero(labelled & np.array([d.year == year for d in ds.as_of]))
        if len(train) < 2000 or len(test) < 200:
            continue
        booster, calibrator, info, quantiles, baseline, n_cal = _fit_window(ds, train)
        p = calibrator.apply(np.asarray(booster.predict(ds.X[test])))
        p_base = baseline.predict(ds.X[test])
        test_dates = [ds.as_of[i] for i in test]
        q = {a: np.asarray(m.predict(ds.X[test])) for a, m in quantiles.items()}
        folds.append(
            FoldResult(
                year, len(train), n_cal, len(test), info,
                {**classification_metrics(test_dates, p, ds.excess[test]),
                 "quantiles": quantile_metrics(ds.excess[test], q)},
                classification_metrics(test_dates, p_base, ds.excess[test]),
            )
        )  # fmt: skip
        pooled["index"].extend(test.tolist())
        pooled["p"].extend(p.tolist())
        pooled["p_base"].extend(p_base.tolist())
        for a in QUANTILES:
            pooled[f"q{a}"].extend(q[a].tolist())
    if not pooled["index"]:
        return folds, {}
    idx = np.array(pooled["index"])
    p, p_base = np.array(pooled["p"]), np.array(pooled["p_base"])
    dates = [ds.as_of[i] for i in idx]
    summary = {
        "model": classification_metrics(dates, p, ds.excess[idx]),
        "baseline": classification_metrics(dates, p_base, ds.excess[idx]),
        "quantiles": quantile_metrics(
            ds.excess[idx], {a: np.array(pooled[f"q{a}"]) for a in QUANTILES}
        ),
        "calibration_curve": calibration_bins(p, (ds.excess[idx] > 0).astype(float)),
        "by_regime": segment_metrics([ds.regime[i] for i in idx], dates, p, ds.excess[idx]),
        "by_sector": segment_metrics([ds.sector[i] for i in idx], dates, p, ds.excess[idx]),
        "test_years": [f.year for f in folds],
    }
    return folds, summary


def shap_importance(booster: lgb.Booster, X: Matrix, names: list[str]) -> list[dict[str, Any]]:
    contributions = np.asarray(booster.predict(X, pred_contrib=True))[:, :-1]
    mean_abs = np.abs(contributions).mean(axis=0)
    order = np.argsort(-mean_abs)
    return [{"feature": names[i], "mean_abs_shap": float(mean_abs[i])} for i in order]


def _jsonable(value: Any) -> Any:
    return json.loads(
        json.dumps(value, default=lambda o: o.isoformat() if isinstance(o, date) else str(o))
    )


def train_and_store(db: Session, benchmark: ReturnSeries, horizon: int) -> ModelVersion | None:
    """Walk-forward evaluate, fit the serving model on all labelled data, store a version."""
    ds = assemble(db, horizon, benchmark)
    labelled = np.flatnonzero(~np.isnan(ds.excess))
    if len(labelled) < 5000:
        logger.warning("model_training_skipped", extra={"horizon": horizon, "rows": len(labelled)})
        return None
    folds, summary = walk_forward(ds)
    booster, calibrator, info, quantiles, _baseline, _n_cal = _fit_window(ds, labelled)
    recent_cut = max(ds.as_of) - timedelta(days=365)
    recent = np.flatnonzero(np.array([d >= recent_cut for d in ds.as_of]))
    artifact = {
        "direction": booster.model_to_string(),
        **{f"q{a}": m.model_to_string() for a, m in quantiles.items()},
    }
    version = ModelVersion(
        name=f"excess_{horizon}d",
        target=f"P(total return beats SPY over {horizon} trading days)",
        horizon_days=horizon,
        feature_set_version=FEATURE_SET_VERSION,
        universe_version=UNIVERSE_VERSION,
        code_version=CODE_VERSION,
        trained_from=min(ds.as_of[i] for i in labelled),
        trained_through=max(ds.as_of[i] for i in labelled),
        feature_names=ds.feature_names,
        params={**LGB_PARAMS, "rounds": ROUNDS, "embargo_days": EMBARGO_DAYS,
                "calibration_months": CALIBRATION_MONTHS, "quantiles": list(QUANTILES)},
        evaluation=_jsonable({"summary": summary, "folds": [f.__dict__ for f in folds]}),
        calibration=_jsonable({"method": calibrator.method, "params": calibrator.params, **info}),
        importance=_jsonable({"shap": shap_importance(booster, ds.X[recent], ds.feature_names)}),
        artifact=json.dumps(artifact),
    )  # fmt: skip
    db.execute(
        ModelVersion.__table__.update()  # type: ignore[attr-defined]
        .where(ModelVersion.name == version.name, ModelVersion.status == "active")
        .values(status="retired")
    )
    db.add(version)
    db.commit()
    return version


def load_boosters(version: ModelVersion) -> dict[str, lgb.Booster]:
    artifact = json.loads(version.artifact)
    return {key: lgb.Booster(model_str=text) for key, text in artifact.items()}


def calibrator_of(version: ModelVersion) -> Calibrator:
    return Calibrator(
        version.calibration.get("method", "none"), version.calibration.get("params") or {}
    )


def latest_rows(ds: Dataset, day: date) -> Iterable[int]:
    return (i for i, d in enumerate(ds.as_of) if d == day)
