import { describe, expect, it } from "vitest";
import { buildRequest, formFrom, fromInput, gridRange, heatTone, toInput, type ValuationDefaults } from "./valuation";

const DEFAULTS = {
  ticker: "EXDV",
  recommended: "dcf",
  available: ["dcf", "rim"],
  unavailable: { ddm: "The company reports no dividends per share." },
  dcf: {
    base_revenue: 1_200_000_000, growth: 0.2121, margin: 0.1667, long_run_margin: 0.1591, terminal_growth: 0.025,
    tax_rate: 0.21, sales_to_capital: 1.043, terminal_roic: null, debt: 500_000_000, cash: 350_000_000,
    shares: 60_000_000, mid_year: false,
  },
  rim: { book_value: 1_000_000_000, roe: 0.1667, long_run_roe: null, payout_ratio: 0, terminal_growth: 0.025, shares: 60_000_000 },
  ddm: null,
  capm: {
    risk_free: 0.0437, beta: 1, equity_risk_premium: 0.05, pre_tax_cost_of_debt: 0.0587,
    equity_value: 1_000_000_000, debt_value: 500_000_000,
  },
  scenarios: {
    bear: { growth: -0.03, margin: -0.02, discount_rate: 0.01, terminal_growth: -0.005 },
    bull: { growth: 0.03, margin: 0.02, discount_rate: -0.01, terminal_growth: 0.005 },
  },
  sources: { growth: "revenue growth, FY2024", risk_free: "US Treasury 10 Yr par yield 2026-09-30" },
  warnings: [],
  price: null,
  price_date: null,
  basis: "TTM Q4 FY2024",
  formula_version: "2026.10-1",
} satisfies ValuationDefaults;

describe("valuation form", () => {
  it("shows rates as percentages and amounts in millions", () => {
    expect(toInput(0.2121, "percent")).toBe("21.21");
    expect(toInput(1_200_000_000, "millions")).toBe("1200");
    expect(toInput(-0.005, "points")).toBe("-0.5");
    expect(toInput(null, "percent")).toBe("");
    expect(fromInput("21.21%", "percent")).toBeCloseTo(0.2121, 10);
    expect(fromInput("1,200", "millions")).toBe(1_200_000_000);
    expect(fromInput("", "percent")).toBeNull();
    expect(fromInput("abc", "percent")).toBeUndefined();
  });

  it("round-trips the defaults into the same request", () => {
    const form = formFrom(DEFAULTS);
    const { request, errors } = buildRequest("dcf", form, DEFAULTS.sources, form);
    expect(errors).toEqual({});
    expect(request?.dcf).toEqual(DEFAULTS.dcf);
    expect(request?.capm).toEqual(DEFAULTS.capm);
    expect(request?.scenarios).toEqual(DEFAULTS.scenarios);
    expect(request?.discount_rate_override).toBeNull();
    expect(request?.rim).toBeUndefined();
    expect(request?.sources).toEqual(DEFAULTS.sources);
  });

  it("records edited inputs and rejects invalid ones", () => {
    const initial = formFrom(DEFAULTS);
    const edited = { ...initial, "dcf.growth": "8", "capm.beta": "" };
    const invalid = buildRequest("dcf", edited, DEFAULTS.sources, initial);
    expect(invalid.request).toBeNull();
    expect(invalid.errors).toEqual({ "capm.beta": "Required" });

    const valid = buildRequest("dcf", { ...edited, "capm.beta": "1.2" }, DEFAULTS.sources, initial);
    expect(valid.request?.dcf?.growth).toBeCloseTo(0.08, 10);
    expect(valid.request?.sources?.growth).toBe("edited (default 21.21%)");
    expect(valid.request?.sources?.risk_free).toBe(DEFAULTS.sources.risk_free);
  });
});

describe("heatmap tones", () => {
  it("diverges around the price", () => {
    expect(heatTone(60, 100, [0, 0])).toBe("below-2");
    expect(heatTone(85, 100, [0, 0])).toBe("below-1");
    expect(heatTone(105, 100, [0, 0])).toBe("near");
    expect(heatTone(125, 100, [0, 0])).toBe("above-1");
    expect(heatTone(140, 100, [0, 0])).toBe("above-2");
    expect(heatTone(null, 100, [0, 0])).toBe("none");
  });

  it("uses thirds of the grid range without a price", () => {
    const range = gridRange([[10, null], [20, 40]]);
    expect(range).toEqual([10, 40]);
    expect(heatTone(12, null, range)).toBe("low");
    expect(heatTone(25, null, range)).toBe("mid");
    expect(heatTone(39, null, range)).toBe("high");
  });
});
