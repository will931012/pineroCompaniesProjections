"""Responses for the Markets and Bitcoin pages, company research tabs, and the Models page."""

import bisect
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import technicals as t
from app.core.config import Settings
from app.db.models import (
    Company,
    CryptoPrice,
    FactorScore,
    FeatureSnapshot,
    FinancialFact,
    InstitutionalHolding,
    MacroObservation,
    MarketRate,
    MarketRegime,
    ModelVersion,
    OwnershipSummary,
    PriceCoverage,
    Security,
    UniverseMember,
)
from app.fundamentals.service import load_prices
from app.fundamentals.statements import shares_for_market_cap
from app.quant import bitcoin as btc
from app.quant import schemas as s
from app.quant.factors import FACTOR_VERSION, LABELS
from app.quant.features import FEATURE_SET_VERSION
from app.quant.journal import history_for_company, journal_summary, latest_for_company
from app.quant.macro import CATALOG, MacroHistory, dashboard
from app.quant.prices import MARKET_TICKERS, ReturnSeries, return_series
from app.quant.regime import REGIME_VERSION, classify
from app.quant.universe import UNIVERSE_VERSION, survivorship
from app.valuation.service import RATE_SERIES

ETF_NAMES = {
    "SPY": ("S&P 500", "index"),
    "QQQ": ("Nasdaq-100", "index"),
    "IWM": ("Russell 2000", "index"),
    "DIA": ("Dow Jones Industrials", "index"),
    "XLK": ("Technology", "sector"),
    "XLF": ("Financials", "sector"),
    "XLE": ("Energy", "sector"),
    "XLV": ("Health care", "sector"),
    "XLY": ("Consumer discretionary", "sector"),
    "XLP": ("Consumer staples", "sector"),
    "XLI": ("Industrials", "sector"),
    "XLB": ("Materials", "sector"),
    "XLU": ("Utilities", "sector"),
    "XLRE": ("Real estate", "sector"),
    "XLC": ("Communication services", "sector"),
    "GLD": ("Gold", "other"),
    "TLT": ("20+ year Treasuries", "other"),
    "IEF": ("7–10 year Treasuries", "other"),
}
TENOR_MONTHS = {
    "1 Mo": 1,
    "1.5 Month": 1.5,
    "2 Mo": 2,
    "3 Mo": 3,
    "4 Mo": 4,
    "6 Mo": 6,
    "1 Yr": 12,
    "2 Yr": 24,
    "3 Yr": 36,
    "5 Yr": 60,
    "7 Yr": 84,
    "10 Yr": 120,
    "20 Yr": 240,
    "30 Yr": 360,
}
TIINGO_NOTE = (
    "Prices: Tiingo end-of-day data, licensed for internal use only on the free plan; total "
    "returns include dividends and splits."
)
FRED_NOTE = (
    "Macro data: FRED®, Federal Reserve Bank of St. Louis (this product uses the FRED API but "
    "is not endorsed or certified by the Bank). Values are as published on each date."
)


def _security(db: Session, ticker: str) -> Security | None:
    return db.scalar(select(Security).where(Security.ticker == ticker, Security.is_active))


def _series(db: Session, ticker: str) -> ReturnSeries | None:
    security = _security(db, ticker)
    if security is None:
        return None
    series = return_series(db, security.id)
    return series if series.dates else None


def _period_returns(series: ReturnSeries) -> dict[str, float | None]:
    index = series.index
    year_start = [i for i, d in enumerate(series.dates) if d.year < series.dates[-1].year]
    return {
        "1d": t.period_return(index, 1),
        "1w": t.period_return(index, 5),
        "1m": t.period_return(index, 21),
        "3m": t.period_return(index, 63),
        "ytd": index[-1] / index[year_start[-1]] - 1 if year_start else None,
        "1y": t.period_return(index, 252),
        "3y": t.period_return(index, 756),
    }


def _curve(db: Session, on_or_before: date | None) -> s.CurveOut | None:
    query = select(func.max(MarketRate.observed_on)).where(MarketRate.series == RATE_SERIES)
    if on_or_before:
        query = query.where(MarketRate.observed_on <= on_or_before)
    day = db.scalar(query)
    if day is None:
        return None
    rows = db.execute(
        select(MarketRate.tenor, MarketRate.value).where(
            MarketRate.series == RATE_SERIES, MarketRate.observed_on == day
        )
    ).all()
    points = sorted(
        (
            s.CurvePoint(tenor=r.tenor, months=TENOR_MONTHS[r.tenor], value=float(r.value))
            for r in rows
            if r.tenor in TENOR_MONTHS
        ),
        key=lambda p: p.months,
    )
    return s.CurveOut(observed_on=day, points=points)


def markets_overview(db: Session, settings: Settings) -> s.MarketsOut:
    etfs = []
    for ticker in MARKET_TICKERS:
        name, group = ETF_NAMES[ticker]
        series = _series(db, ticker)
        if series is None:
            etfs.append(
                s.EtfOut(
                    ticker=ticker,
                    name=name,
                    group=group,
                    close=None,
                    price_date=None,
                    returns={},
                    spark=[],
                )
            )
            continue
        index = series.index[-253:]
        dates = series.dates[-253:]
        step = max(1, len(index) // 60)
        etfs.append(
            s.EtfOut(
                ticker=ticker,
                name=name,
                group=group,
                close=series.closes[-1],
                price_date=series.dates[-1],
                returns=_period_returns(series),
                spark=[
                    s.SeriesPoint(date=d, value=v / index[0])
                    for d, v in list(zip(dates, index, strict=True))[::step]
                ],
            )
        )
    curve = _curve(db, None)
    macro_history = MacroHistory(db)
    today = date.today()
    macro = [
        s.MacroSeriesOut(
            **{k: v for k, v in row.items() if k != "history"},
            history=[s.SeriesPoint(date=d, value=v) for d, v in row.get("history", [])],
        )
        for row in dashboard(macro_history, today)
    ]
    for item in macro:
        item.history = item.history[-260:]
    spy = _series(db, "SPY")
    regime = None
    if spy is not None:
        current = classify(spy, macro_history, spy.dates[-1])
        if current:
            history = db.execute(
                select(MarketRegime.as_of, MarketRegime.label)
                .where(MarketRegime.version == REGIME_VERSION)
                .order_by(MarketRegime.as_of)
            ).all()
            regime = s.RegimeOut(
                as_of=current.as_of,
                label=current.label,
                components=current.components,
                history=[(r.as_of, r.label) for r in history],
            )
    bitcoin = btc.bitcoin_series(db)
    tile = None
    if len(bitcoin.closes) > 31:
        summary = btc.summary(bitcoin)
        tile = s.BitcoinTile(
            close=summary["close"],
            as_of=summary["as_of"],
            change_1d=summary["returns"]["1d"],
            change_30d=summary["returns"]["30d"],
            drawdown=summary["drawdown"],
        )
    return s.MarketsOut(
        etfs=etfs,
        curve=curve,
        curve_year_ago=_curve(db, curve.observed_on - timedelta(days=365)) if curve else None,
        macro=macro,
        macro_configured=settings.fred_api_key is not None,
        regime=regime,
        bitcoin=tile,
        notes=[
            TIINGO_NOTE,
            FRED_NOTE,
            "Yield curve: U.S. Treasury daily par yield curve rates (public domain).",
        ],
    )


def _as_daily(series: ReturnSeries | None) -> btc.Daily:
    if series is None:
        return btc.Daily([], [])
    return btc.Daily(series.dates, series.index)


def bitcoin_view(db: Session) -> s.BitcoinOut:
    series = btc.bitcoin_series(db)
    source = "Tiingo crypto prices (btcusd), daily UTC bars aggregated across exchanges."
    note = (
        "The next halving is expected at block 1,050,000, around 2028; its exact date depends on "
        "block times, so it is not shown as a fact."
    )
    if len(series.closes) < 400:
        return s.BitcoinOut(
            available=False,
            summary={},
            series=[],
            rsi_14=[],
            relationships=[],
            halvings=[],
            next_halving_note=note,
            source=source,
            notes=["Bitcoin prices have not been loaded yet."],
        )
    sma50, sma200 = t.sma(series.closes, 50), t.sma(series.closes, 200)
    drawdown = t.drawdowns(series.closes)
    rsi = t.rsi(series.closes, 14)
    points = [
        s.BitcoinPoint(date=d, close=c, sma_50=a, sma_200=b, drawdown=dd)
        for d, c, a, b, dd in zip(series.dates, series.closes, sma50, sma200, drawdown, strict=True)
    ]
    relationships = []
    for name, other, level in (
        ("S&P 500 (SPY)", _as_daily(_series(db, "SPY")), False),
        ("Gold (GLD)", _as_daily(_series(db, "GLD")), False),
        ("10-year Treasury yield", btc.ten_year_yield(db), True),
    ):
        if len(other.dates) > 200:
            r = btc.relationship(name, series, other, level=level)
            relationships.append(s.RelationshipOut(**r.__dict__))
    return s.BitcoinOut(
        available=True,
        summary=btc.summary(series),
        series=points,
        rsi_14=list(zip(series.dates, rsi, strict=True))[-730:],
        relationships=relationships,
        halvings=[s.HalvingOut(**c.__dict__) for c in btc.halving_cycles(series)],
        next_halving_note=note,
        source=source,
        notes=[
            "Bitcoin trades every day; volatility is annualised over 365 days. Correlations use "
            "only days both markets were open; the yield comparison uses daily changes in yield.",
            TIINGO_NOTE,
        ],
    )


def technicals(db: Session, security: Security, years: int = 3) -> s.TechnicalsOut:
    source = "Tiingo end-of-day prices; split- and dividend-adjusted from raw closes."
    series = return_series(db, security.id)
    if len(series.dates) < 60:
        return s.TechnicalsOut(
            ticker=security.ticker,
            available=False,
            price_date=None,
            stats={},
            series=[],
            source=source,
            message="Not enough stored price history (needs about three months).",
        )
    index = series.index
    adjusted = [v * series.closes[-1] / index[-1] for v in index]
    sma50, sma200, rsi = t.sma(adjusted, 50), t.sma(adjusted, 200), t.rsi(adjusted, 14)
    macd = t.macd(adjusted)
    drawdown = t.drawdowns(adjusted)
    spy = _series(db, "SPY")
    start = series.dates[-1] - timedelta(days=365 * years)
    first = bisect.bisect_left(series.dates, start)
    relative: list[float | None] = [None] * len(series.dates)
    beta = None
    if spy is not None:
        spy_index = dict(zip(spy.dates, spy.index, strict=True))
        base = None
        for i in range(first, len(series.dates)):
            m = spy_index.get(series.dates[i])
            if m is None:
                continue
            if base is None:
                base = index[i] / m
            relative[i] = index[i] / m / base
        spy_returns = dict(zip(spy.dates[1:], spy.returns, strict=True))
        pairs = [
            (r, spy_returns[d])
            for d, r in zip(series.dates[1:], series.returns, strict=True)
            if d in spy_returns
        ][-252:]
        beta = t.beta([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) > 100 else None
    window = adjusted[-252:]
    stats: dict[str, float | None] = {
        **{f"return_{k}": v for k, v in _period_returns(series).items()},
        "vol_1m": t.annualised_volatility(series.returns[-21:]),
        "vol_3m": t.annualised_volatility(series.returns[-63:]),
        "vol_1y": t.annualised_volatility(series.returns[-252:]),
        "beta_1y": beta,
        "max_drawdown_1y": t.max_drawdown(window),
        "high_52w": max(window),
        "low_52w": min(window),
        "dist_sma_50": adjusted[-1] / sma50[-1] - 1 if sma50[-1] else None,
        "dist_sma_200": adjusted[-1] / sma200[-1] - 1 if sma200[-1] else None,
        "rsi_14": rsi[-1],
    }
    points = [
        s.TechnicalPoint(
            date=series.dates[i],
            close=adjusted[i],
            sma_50=sma50[i],
            sma_200=sma200[i],
            rsi_14=rsi[i],
            macd=macd.line[i],
            macd_signal=macd.signal[i],
            drawdown=drawdown[i],
            relative=relative[i],
        )
        for i in range(first, len(series.dates))
    ]
    return s.TechnicalsOut(
        ticker=security.ticker,
        available=True,
        message=None,
        price_date=series.dates[-1],
        stats=stats,
        series=points,
        source=source,
    )


def factors(db: Session, company: Company) -> s.FactorsOut:
    note = (
        "Scores compare the company with the model universe (the largest companies by revenue) on "
        "the same month end: each input is winsorised and z-scored, and a factor is the average "
        "of its inputs. They describe the company; they are not forecasts."
    )
    latest_universe = db.scalar(
        select(func.max(UniverseMember.as_of)).where(UniverseMember.version == UNIVERSE_VERSION)
    )
    member = (
        db.scalar(
            select(UniverseMember).where(
                UniverseMember.version == UNIVERSE_VERSION,
                UniverseMember.as_of == latest_universe,
                UniverseMember.company_id == company.id,
            )
        )
        if latest_universe
        else None
    )
    day = db.scalar(
        select(func.max(FactorScore.as_of)).where(
            FactorScore.version == FACTOR_VERSION, FactorScore.company_id == company.id
        )
    )
    size = (
        db.scalar(
            select(func.count()).where(
                UniverseMember.version == UNIVERSE_VERSION, UniverseMember.as_of == latest_universe
            )
        )
        if latest_universe
        else None
    )
    if day is None:
        return s.FactorsOut(
            ticker="",
            as_of=None,
            in_universe=member is not None,
            universe_rank=member.rank if member else None,
            universe_size=size,
            factors=[],
            note=note,
        )
    rows = db.scalars(
        select(FactorScore)
        .where(FactorScore.version == FACTOR_VERSION, FactorScore.company_id == company.id)
        .order_by(FactorScore.as_of)
    ).all()
    history: dict[str, list[s.SeriesPoint]] = {}
    for r in rows:
        history.setdefault(r.factor, []).append(s.SeriesPoint(date=r.as_of, value=r.percentile))
    latest = [r for r in rows if r.as_of == day]
    out = [
        s.FactorOut(
            key=r.factor,
            label=LABELS.get(r.factor, r.factor),
            score=r.score,
            percentile=r.percentile,
            inputs=[
                s.FactorInput(feature=k, value=v.get("value"), z=v["z"])
                for k, v in r.inputs.items()
            ],
            history=history.get(r.factor, [])[-60:],
        )
        for r in sorted(latest, key=lambda r: list(LABELS).index(r.factor))
    ]
    return s.FactorsOut(
        ticker="",
        as_of=day,
        in_universe=member is not None,
        universe_rank=member.rank if member else None,
        universe_size=size,
        factors=out,
        note=note,
    )


def ownership(db: Session, company: Company) -> s.OwnershipOut:
    note = (
        "Form 13F: long positions in U.S. equities reported quarterly by managers with over "
        "$100 million, filed up to 45 days after the quarter. Shorts, options, and smaller "
        "holders are not included. Holder changes compare each manager's reported shares "
        "between the two quarters; only the 100 largest holders per quarter are stored. Positions "
        "whose value per share is over 5× from the quarter's median are excluded (mostly managers "
        "still reporting in thousands, about 4–5% of positions but under 0.5% of value)."
    )
    summaries = db.scalars(
        select(OwnershipSummary)
        .where(OwnershipSummary.company_id == company.id)
        .order_by(OwnershipSummary.period_of_report.desc())
        .limit(4)
    ).all()
    if not summaries:
        return s.OwnershipOut(
            ticker="", available=False, periods=[], holders=[], sold_out=[], cusips=[], note=note
        )
    prices = load_prices(db, company)
    facts = db.scalars(select(FinancialFact).where(FinancialFact.company_id == company.id)).all()
    periods = []
    for summary in summaries:
        share = None
        on_or_before = [p for p in prices if p.trade_date <= summary.period_of_report]
        shares = shares_for_market_cap(facts, summary.period_of_report, summary.period_of_report)
        if on_or_before and shares and len(summary.cusips) == 1:
            cap = float(on_or_before[-1].close) * float(shares.value)
            share = float(summary.value_usd) / cap if cap > 0 else None
        periods.append(
            s.OwnershipPeriodOut(
                period=summary.period_of_report,
                holders=summary.holders,
                shares=float(summary.shares),
                value_usd=float(summary.value_usd),
                share_of_market_cap=share,
            )
        )
    latest = summaries[0].period_of_report
    previous = summaries[1].period_of_report if len(summaries) > 1 else None

    def holdings(period: date | None) -> dict[int, InstitutionalHolding]:
        if period is None:
            return {}
        return {
            h.filer_cik: h
            for h in db.scalars(
                select(InstitutionalHolding).where(
                    InstitutionalHolding.company_id == company.id,
                    InstitutionalHolding.period_of_report == period,
                )
            )
        }

    now, before = holdings(latest), holdings(previous)

    def holder(h: InstitutionalHolding, previous_shares: float | None, change: str) -> s.HolderOut:
        return s.HolderOut(
            filer_name=h.filer_name,
            filer_cik=h.filer_cik,
            shares=float(h.shares),
            value_usd=float(h.value_usd),
            previous_shares=previous_shares,
            change=change,
            filing_date=h.filing_date,
            accession=h.accession,
        )

    out = []
    for h in sorted(now.values(), key=lambda h: -h.value_usd):
        old = before.get(h.filer_cik)
        if previous is None:
            change = "unknown"
        elif old is None:
            change = "entered_top"
        elif h.shares > old.shares:
            change = "increased"
        elif h.shares < old.shares:
            change = "decreased"
        else:
            change = "unchanged"
        out.append(holder(h, float(old.shares) if old else None, change))
    left = [
        holder(h, float(h.shares), "left_top") for h in before.values() if h.filer_cik not in now
    ]
    left.sort(key=lambda h: -h.value_usd)
    return s.OwnershipOut(
        ticker="",
        available=True,
        periods=periods,
        holders=out,
        sold_out=left[:25],
        cusips=summaries[0].cusips,
        note=note,
    )


# A model's output is offered as guidance only if, out of sample, it separates winners from
# losers (AUC), its probabilities beat always predicting the base rate (Brier), and its monthly
# ranking skill is statistically distinguishable from zero (rank IC t-stat). Fixed in advance.
VALIDATION_RULE = "out-of-sample AUC > 0.52, Brier below the base rate, and rank IC t-stat > 2"


def validated(oos: dict[str, Any]) -> bool:
    auc, brier, base, t = (
        oos.get("auc"),
        oos.get("brier"),
        oos.get("brier_base_rate"),
        oos.get("rank_ic_t"),
    )
    return (
        auc is not None and brier is not None and base is not None and t is not None
        and auc > 0.52 and brier < base and t > 2
    )  # fmt: skip


def _oos(version: ModelVersion) -> dict[str, Any]:
    summary = (version.evaluation or {}).get("summary") or {}
    model, base = summary.get("model") or {}, summary.get("baseline") or {}
    ic, base_ic = model.get("rank_ic") or {}, base.get("rank_ic") or {}
    out = {
        "auc": model.get("auc"),
        "brier": model.get("brier"),
        "brier_base_rate": model.get("brier_base_rate"),
        "rank_ic_mean": ic.get("mean"),
        "rank_ic_t": ic.get("t_stat"),
        "decile_spread": model.get("decile_spread"),
        "rows": model.get("rows"),
        "baseline_auc": base.get("auc"),
        "baseline_rank_ic_mean": base_ic.get("mean"),
        "test_years": summary.get("test_years"),
        "coverage_10_90": (summary.get("quantiles") or {}).get("coverage_10_90"),
    }
    out["validated"] = validated(out)
    out["validation_rule"] = VALIDATION_RULE
    return out


def model_summary(db: Session, version: ModelVersion) -> s.ModelSummary:
    return s.ModelSummary(
        id=version.id,
        name=version.name,
        target=version.target,
        horizon_days=version.horizon_days,
        status=version.status,
        created_at=version.created_at,
        trained_from=version.trained_from,
        trained_through=version.trained_through,
        feature_set_version=version.feature_set_version,
        code_version=version.code_version,
        oos=_oos(version),
        live=journal_summary(db, version),
    )


def model_detail(db: Session, version: ModelVersion) -> s.ModelDetail:
    return s.ModelDetail(
        **model_summary(db, version).model_dump(),
        params=version.params,
        evaluation=version.evaluation,
        calibration=version.calibration,
        importance=version.importance,
        feature_names=version.feature_names,
    )


def predictions(db: Session, company: Company) -> s.PredictionsOut:
    disclaimer = (
        "Model output, not advice. A model is shown as guidance only when it passes the "
        f"validation rule ({VALIDATION_RULE}); otherwise its predictions are journaled and "
        "scored but marked not validated. Ranges are the 10th–90th percentile of the forecast."
    )
    in_universe = db.scalar(
        select(func.count()).where(
            UniverseMember.company_id == company.id, UniverseMember.version == UNIVERSE_VERSION
        )
    )
    out = []
    for prediction, version in latest_for_company(db, company.id):
        oos = _oos(version)
        out.append(
            s.PredictionOut(
                model_id=version.id,
                model_name=version.name,
                target=version.target,
                horizon_days=version.horizon_days,
                as_of=prediction.as_of,
                probability=prediction.probability,
                probability_raw=prediction.probability_raw,
                expected_excess=prediction.expected_excess,
                excess_low=prediction.excess_low,
                excess_high=prediction.excess_high,
                drivers=[s.DriverOut(**d) for d in prediction.drivers],
                data_available_on=prediction.data_available_on,
                trained_through=version.trained_through,
                calibration=str(version.calibration.get("method", "none")),
                model_oos_auc=oos["auc"],
                validated=oos["validated"],
                model_oos_rank_ic=oos["rank_ic_mean"],
            )
        )
    history = [
        s.PredictionHistoryOut(
            as_of=p.as_of,
            model_name=name,
            probability=p.probability,
            expected_excess=p.expected_excess,
            realised_excess=o.excess_return if o else None,
            went_up=o.went_up if o else None,
        )
        for p, o, name in history_for_company(db, company.id)
    ]
    return s.PredictionsOut(
        ticker="",
        in_universe=bool(in_universe),
        predictions=out,
        history=history,
        disclaimer=disclaimer,
    )


def research_status(db: Session, settings: Settings) -> s.ResearchStatus:
    stats = survivorship(db)
    latest = stats[-1] if stats else None
    coverage = dict(
        db.execute(select(PriceCoverage.status, func.count()).group_by(PriceCoverage.status)).all()
    )
    from app.quant.pipeline import price_targets

    feature_dates = db.scalars(
        select(FeatureSnapshot.as_of)
        .where(FeatureSnapshot.version == FEATURE_SET_VERSION)
        .distinct()
    ).all()
    return s.ResearchStatus(
        universe_dates=len(stats),
        latest_universe_date=latest["as_of"] if latest else None,  # type: ignore[arg-type]
        members_latest=int(latest["members"]) if latest else 0,  # type: ignore[call-overload]
        members_with_prices_latest=int(latest["with_prices"]) if latest else 0,  # type: ignore[call-overload]
        survivorship=stats[-180:],
        price_targets=len(price_targets(db)),
        prices_loaded=int(coverage.get("loaded", 0)),
        prices_not_found=int(coverage.get("not_found", 0)),
        feature_dates=len(feature_dates),
        latest_feature_date=max(feature_dates) if feature_dates else None,
        fred_configured=settings.fred_api_key is not None,
        macro_series_loaded=int(
            db.scalar(select(func.count(func.distinct(MacroObservation.series_id)))) or 0
        ),
        bitcoin_days=int(db.scalar(select(func.count()).select_from(CryptoPrice)) or 0),
        ownership_periods=list(
            db.scalars(
                select(OwnershipSummary.period_of_report)
                .distinct()
                .order_by(OwnershipSummary.period_of_report.desc())
            )
        ),
        models=int(db.scalar(select(func.count()).select_from(ModelVersion)) or 0),
    )


CATALOG_IDS = [spec.series_id for spec in CATALOG]
