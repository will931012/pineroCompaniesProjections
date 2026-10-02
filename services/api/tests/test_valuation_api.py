from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.analytics.valuation import DcfAssumptions, WaccInputs, dcf
from app.companies.routes import get_sec_client
from app.core.config import Settings, get_settings
from app.db.models import Company, MarketRate, ProviderFetch
from app.market_data.routes import configured_provider
from app.providers.treasury import TreasuryClient
from app.valuation.routes import get_treasury
from tests.conftest import LoggedIn
from tests.fakes import json_response, sec_client_with, tiingo_row, tiingo_with
from tests.fixtures_companyfacts import CIK, companyfacts

pytestmark = pytest.mark.integration

PEER_CIK = 999002
ROWS = [
    [CIK, "Example Devices Inc.", "EXDV", "Nasdaq"],
    [PEER_CIK, "Example Peer Corp", "EXPR", "NYSE"],
]
FACTS = {CIK: companyfacts(), PEER_CIK: companyfacts(scale=2)}
YIELDS = (
    'Date,"1 Mo","3 Mo","10 Yr","30 Yr"\n'
    "09/30/2026,4.10,4.05,4.37,4.70\n"
    "09/29/2026,4.11,4.06,4.52,4.71\n"
)


def treasury_returning(status: int, calls: list[httpx.Request]) -> TreasuryClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text=YIELDS if status == 200 else "")

    return TreasuryClient(httpx.Client(transport=httpx.MockTransport(handler)), "test agent")


@pytest.fixture
def treasury_calls(override) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    calls: list[httpx.Request] = []
    client = treasury_returning(200, calls)
    override(get_treasury, lambda: client)
    return calls


@pytest.fixture
def loaded(analyst: LoggedIn, override, seed_directory, treasury_calls) -> LoggedIn:  # type: ignore[no-untyped-def]
    seed_directory(ROWS)
    override(get_sec_client, lambda: sec_client_with(facts=FACTS))
    for ticker in ("EXDV", "EXPR"):
        assert analyst.client.get(f"/api/v1/companies/{ticker}/fundamentals").status_code == 200
    return analyst


def defaults(user: LoggedIn, ticker: str = "EXDV") -> dict[str, Any]:
    response = user.client.get(f"/api/v1/companies/{ticker}/valuation/defaults")
    assert response.status_code == 200, response.text
    return response.json()


def request_from(body: dict[str, Any], model: str = "dcf", **changes: Any) -> dict[str, Any]:
    return {
        "model": model,
        "dcf": body["dcf"],
        "rim": body["rim"],
        "ddm": body["ddm"],
        "capm": body["capm"],
        "scenarios": body["scenarios"],
        "sources": body["sources"],
        **changes,
    }


def post(user: LoggedIn, path: str, payload: dict[str, Any]) -> httpx.Response:
    return user.client.post(path, json=payload, headers={"X-CSRF-Token": user.csrf})


def test_defaults_come_from_filings_with_sources(
    loaded: LoggedIn,
    treasury_calls: list[httpx.Request],
    db: Session,
) -> None:
    body = defaults(loaded)

    assert body["recommended"] == "dcf" and body["available"] == ["dcf", "rim"]
    assert body["unavailable"] == {"ddm": "The company reports no dividends per share."}
    dcf_in = body["dcf"]
    assert dcf_in["base_revenue"] == 1200.0
    assert dcf_in["growth"] == pytest.approx(1200 / 990 - 1, abs=1e-4)
    assert dcf_in["margin"] == pytest.approx(200 / 1200, abs=1e-4)
    assert dcf_in["long_run_margin"] == pytest.approx((150 / 990 + 200 / 1200) / 2, abs=1e-4)
    assert dcf_in["sales_to_capital"] == pytest.approx(1200 / (1000 + 500 - 350), abs=1e-3)
    assert (dcf_in["debt"], dcf_in["cash"], dcf_in["shares"]) == (500.0, 350.0, 60.0)
    assert body["rim"]["roe"] == pytest.approx(150 / 900, abs=1e-4)
    assert body["capm"] == {
        "risk_free": 0.0437,
        "beta": 1.0,
        "equity_risk_premium": 0.05,
        "pre_tax_cost_of_debt": pytest.approx(0.0587),
        "equity_value": 1000.0,
        "debt_value": 500.0,
    }
    # Each default names its source, including the period basis actually used.
    sources = body["sources"]
    assert sources["risk_free"] == "US Treasury 10 Yr par yield 2026-09-30"
    assert sources["base_revenue"] == "revenue, FY2024"
    assert sources["shares"] == "cover-page shares 2024-10-18"
    assert any("Beta is assumed to be 1.0" in w for w in body["warnings"])
    assert any("book equity" in w for w in body["warnings"])

    defaults(loaded)  # the stored rate is reused within its TTL
    assert len(treasury_calls) == 1
    assert db.scalar(select(func.count()).select_from(MarketRate)) == 8
    fetch = db.scalar(select(ProviderFetch).where(ProviderFetch.provider == "us_treasury"))
    assert fetch is not None and fetch.status == "success"


def test_unavailable_treasury_rate_falls_back_with_a_warning(
    analyst: LoggedIn,
    override,
    seed_directory,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    seed_directory(ROWS)
    override(get_sec_client, lambda: sec_client_with(facts=FACTS))
    override(get_treasury, lambda: treasury_returning(503, []))
    analyst.client.get("/api/v1/companies/EXDV/fundamentals")

    body = defaults(analyst)

    assert body["capm"]["risk_free"] == 0.045
    assert "Treasury rate unavailable" in body["sources"]["risk_free"]
    assert any("Treasury yield could not be loaded" in w for w in body["warnings"])
    fetch = db.scalar(select(ProviderFetch).where(ProviderFetch.provider == "us_treasury"))
    assert fetch is not None and fetch.status == "error"


def test_financial_companies_default_to_residual_income(loaded: LoggedIn, db: Session) -> None:
    db.execute(update(Company).where(Company.cik == CIK).values(sic_code="6021"))
    db.commit()

    body = defaults(loaded)

    assert body["recommended"] == "rim"
    assert any("residual income is recommended" in w for w in body["warnings"])


def test_compute_matches_the_engine_and_orders_scenarios(loaded: LoggedIn) -> None:
    body = defaults(loaded)

    response = post(loaded, "/api/v1/companies/EXDV/valuation/compute", request_from(body))

    assert response.status_code == 200, response.text
    result = response.json()
    capm, dcf_in = body["capm"], body["dcf"]
    wacc = WaccInputs(**capm, tax_rate=dcf_in["tax_rate"]).wacc()
    expected = dcf(DcfAssumptions(**dcf_in, discount_rate=wacc))
    assert result["rates"]["discount_rate"] == pytest.approx(wacc)
    assert result["rates"]["equity_weight"] == pytest.approx(1000 / 1500)
    assert result["rates"]["discount_rate_source"] == "computed"
    by_name = {s["name"]: s for s in result["scenarios"]}
    assert by_name["base"]["result"]["per_share"] == pytest.approx(expected.per_share)
    assert len(by_name["base"]["result"]["years"]) == 10
    per_share = [by_name[n]["result"]["per_share"] for n in ("bear", "base", "bull")]
    assert per_share == sorted(per_share)
    assert by_name["base"]["upside"] is None  # no stored price
    assert [(g["row_label"], g["column_label"]) for g in result["sensitivity"]] == [
        ("discount_rate", "terminal_growth"),
        ("growth", "margin"),
    ]
    assert result["sensitivity"][0]["values"][2][2] == pytest.approx(expected.per_share)


def test_discount_rate_override_and_residual_income(loaded: LoggedIn) -> None:
    body = defaults(loaded)

    result = post(
        loaded,
        "/api/v1/companies/EXDV/valuation/compute",
        request_from(body, "rim", discount_rate_override=0.09),
    ).json()

    assert result["model"] == "rim"
    assert result["rates"]["discount_rate"] == 0.09
    assert result["rates"]["discount_rate_source"] == "override"
    assert result["rates"]["cost_of_equity"] == pytest.approx(0.0437 + 0.05)
    base = next(s for s in result["scenarios"] if s["name"] == "base")
    # ROE (16.7%) above the cost of equity (9%) is worth more than book (1000 / 60 shares).
    assert base["result"]["per_share"] > 1000 / 60


def test_invalid_assumptions_are_rejected(loaded: LoggedIn) -> None:
    body = defaults(loaded)

    unusable = post(
        loaded,
        "/api/v1/companies/EXDV/valuation/compute",
        request_from(
            body,
            discount_rate_override=0.05,
            dcf={**body["dcf"], "terminal_growth": 0.048},
        ),
    )
    out_of_range = post(
        loaded,
        "/api/v1/companies/EXDV/valuation/compute",
        request_from(body, dcf={**body["dcf"], "growth": 5}),
    )
    missing = post(loaded, "/api/v1/companies/EXDV/valuation/compute", request_from(body, "ddm"))

    assert unusable.status_code == 422
    assert unusable.json()["error"]["code"] == "invalid_assumptions"
    assert "must exceed terminal growth" in unusable.json()["error"]["message"]
    assert out_of_range.status_code == 422
    assert missing.status_code == 422


def test_saved_runs_are_private_to_their_owner(
    loaded: LoggedIn,
    make_user,
    login,  # type: ignore[no-untyped-def]
) -> None:
    body = defaults(loaded)
    saved = post(
        loaded,
        "/api/v1/companies/EXDV/valuation/runs",
        request_from(body, name="Base DCF"),
    )
    assert saved.status_code == 201, saved.text
    run = saved.json()
    other = login(make_user("other@example.com"))

    listed = loaded.client.get("/api/v1/companies/EXDV/valuation/runs").json()
    fetched = loaded.client.get(f"/api/v1/valuation/runs/{run['id']}").json()

    assert [r["name"] for r in listed] == ["Base DCF"]
    assert listed[0]["base_per_share"] == pytest.approx(run["base_per_share"])
    assert fetched["request"]["dcf"] == body["dcf"]
    assert fetched["sources"]["risk_free"] == "US Treasury 10 Yr par yield 2026-09-30"
    assert fetched["formula_version"] == run["results"]["formula_version"]
    assert other.client.get(f"/api/v1/valuation/runs/{run['id']}").status_code == 404
    assert other.client.get("/api/v1/companies/EXDV/valuation/runs").json() == []
    assert (
        other.client.delete(
            f"/api/v1/valuation/runs/{run['id']}", headers={"X-CSRF-Token": other.csrf}
        ).status_code
        == 404
    )
    deleted = loaded.client.delete(
        f"/api/v1/valuation/runs/{run['id']}", headers={"X-CSRF-Token": loaded.csrf}
    )
    assert deleted.status_code == 204
    assert loaded.client.get("/api/v1/companies/EXDV/valuation/runs").json() == []


def test_prices_give_upside_and_relative_valuation(
    loaded: LoggedIn,
    override,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    db.execute(update(Company).where(Company.cik.in_([CIK, PEER_CIK])).values(sic_code="3571"))
    db.commit()
    provider = tiingo_with(
        lambda _: json_response([tiingo_row("2024-10-30", 50.0), tiingo_row("2024-10-31", 52.0)])
    )
    override(configured_provider, lambda: provider)
    override(get_settings, lambda: Settings(market_data_provider="tiingo", tiingo_api_key="k"))  # type: ignore[arg-type]
    for ticker in ("EXDV", "EXPR"):
        loaded.client.get(
            f"/api/v1/market-data/{ticker}/bars", params={"from": "2024-10-01", "to": "2024-10-31"}
        )
        loaded.client.get(f"/api/v1/companies/{ticker}/metrics")

    body = defaults(loaded)
    assert body["price"] == 52.0 and body["capm"]["equity_value"] == 52.0 * 60
    computed = post(loaded, "/api/v1/companies/EXDV/valuation/compute", request_from(body)).json()
    base = next(s for s in computed["scenarios"] if s["name"] == "base")
    assert base["upside"] == pytest.approx(base["result"]["per_share"] / 52.0 - 1)

    relative = loaded.client.get("/api/v1/companies/EXDV/valuation/relative").json()
    assert relative["peers"] == ["EXPR"] and relative["peer_basis"] == "SIC major group 35"
    pe = next(m for m in relative["multiples"] if m["key"] == "pe_ratio")
    # EXDV: 52 × 60 / 150 = 20.8. EXPR has twice the income at the same price: 10.4.
    assert pe["company"] == pytest.approx(20.8)
    assert pe["peer_median"] == pytest.approx(10.4) and pe["peer_count"] == 1
    assert pe["industry_median"] == pytest.approx(10.4)
    assert pe["implied_per_share"] == pytest.approx(10.4 * 150 / 60)
    assert relative["price"] == 52.0
