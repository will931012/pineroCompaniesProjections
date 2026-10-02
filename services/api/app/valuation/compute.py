"""Run a valuation request: base, bear, and bull scenarios plus sensitivity grids."""

from dataclasses import asdict
from typing import Literal

from app.analytics.valuation import (
    Assumptions,
    DcfAssumptions,
    DdmAssumptions,
    RimAssumptions,
    Scenario,
    ValuationError,
    WaccInputs,
    apply_scenario,
    sensitivity,
    value,
)
from app.valuation.schemas import (
    GridOut,
    RatesOut,
    ResultOut,
    ScenarioOut,
    ValuationIn,
)


def assumptions_of(request: ValuationIn) -> tuple[Assumptions, RatesOut]:
    capm = request.capm
    cost_of_equity = capm.risk_free + capm.beta * capm.equity_risk_premium
    override = request.discount_rate_override
    source: Literal["computed", "override"] = "override" if override is not None else "computed"
    if request.model == "dcf":
        if request.dcf is None:
            raise ValuationError("DCF inputs are missing.")
        wacc_inputs = WaccInputs(
            risk_free=capm.risk_free,
            beta=capm.beta,
            equity_risk_premium=capm.equity_risk_premium,
            pre_tax_cost_of_debt=capm.pre_tax_cost_of_debt,
            tax_rate=request.dcf.tax_rate,
            equity_value=capm.equity_value,
            debt_value=capm.debt_value,
        )
        total = capm.equity_value + capm.debt_value
        rate = override if override is not None else wacc_inputs.wacc()
        return DcfAssumptions(**request.dcf.model_dump(), discount_rate=rate), RatesOut(
            cost_of_equity=cost_of_equity,
            after_tax_cost_of_debt=capm.pre_tax_cost_of_debt * (1 - request.dcf.tax_rate),
            equity_weight=capm.equity_value / total if total > 0 else None,
            discount_rate=rate,
            discount_rate_source=source,
        )
    rate = override if override is not None else cost_of_equity
    rates = RatesOut(
        cost_of_equity=cost_of_equity,
        after_tax_cost_of_debt=None,
        equity_weight=None,
        discount_rate=rate,
        discount_rate_source=source,
    )
    if request.model == "rim":
        if request.rim is None:
            raise ValuationError("Residual income inputs are missing.")
        return RimAssumptions(**request.rim.model_dump(), cost_of_equity=rate), rates
    if request.ddm is None:
        raise ValuationError("Dividend discount inputs are missing.")
    return DdmAssumptions(**request.ddm.model_dump(), cost_of_equity=rate), rates


def run(
    request: ValuationIn, price: float | None
) -> tuple[RatesOut, list[ScenarioOut], list[GridOut]]:
    """Raises ValuationError when the base case itself cannot be valued."""
    base, rates = assumptions_of(request)
    shifts = {
        "bear": Scenario(**request.scenarios.bear.model_dump()),
        "base": Scenario(),
        "bull": Scenario(**request.scenarios.bull.model_dump()),
    }
    value(base)  # the base case must be valid; scenarios may individually fail
    scenarios: list[ScenarioOut] = []
    for name, shift in shifts.items():
        try:
            result = value(apply_scenario(base, shift))
        except ValuationError as error:
            scenarios.append(ScenarioOut(name=name, result=None, error=str(error), upside=None))  # type: ignore[arg-type]
            continue
        per_share = result.per_share
        scenarios.append(
            ScenarioOut(
                name=name,  # type: ignore[arg-type]
                result=ResultOut(**asdict(result)),
                error=None,
                upside=per_share / price - 1 if per_share is not None and price else None,
            )
        )
    grids = [GridOut(**asdict(grid)) for grid in sensitivity(base)]
    return rates, scenarios, grids
