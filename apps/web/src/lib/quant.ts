import { formatPercent, formatValue } from "./format";

type FeatureUnit = "fraction" | "ratio" | "index" | "percent_points" | "log_cap";

/** Model inputs: plain-language label and how to show the raw value. */
export const FEATURES: Record<string, { label: string; unit: FeatureUnit }> = {
  mom_1m: { label: "1-month return", unit: "fraction" },
  mom_3m: { label: "3-month return", unit: "fraction" },
  mom_6m: { label: "6-month return", unit: "fraction" },
  mom_12_1: { label: "12-month return, excluding the last month", unit: "fraction" },
  vol_1m: { label: "1-month volatility", unit: "fraction" },
  vol_3m: { label: "3-month volatility", unit: "fraction" },
  beta_1y: { label: "Beta to the S&P 500 (1 year)", unit: "ratio" },
  max_drawdown_1y: { label: "Largest fall in the last year", unit: "fraction" },
  dist_sma200: { label: "Distance from the 200-day average", unit: "fraction" },
  rsi_14: { label: "RSI (14 days)", unit: "index" },
  revenue_growth_yoy: { label: "Revenue growth", unit: "fraction" },
  revenue_cagr_3y: { label: "3-year revenue growth (annual)", unit: "fraction" },
  eps_growth_yoy: { label: "EPS growth", unit: "fraction" },
  gross_margin: { label: "Gross margin", unit: "fraction" },
  operating_margin: { label: "Operating margin", unit: "fraction" },
  net_margin: { label: "Net margin", unit: "fraction" },
  fcf_margin: { label: "Free cash flow margin", unit: "fraction" },
  roe: { label: "Return on equity", unit: "fraction" },
  roa: { label: "Return on assets", unit: "fraction" },
  roic: { label: "Return on invested capital", unit: "fraction" },
  current_ratio: { label: "Current ratio", unit: "ratio" },
  debt_to_equity: { label: "Debt to equity", unit: "ratio" },
  interest_coverage: { label: "Interest coverage", unit: "ratio" },
  earnings_yield: { label: "Earnings yield (1 ÷ P/E)", unit: "fraction" },
  sales_yield: { label: "Sales yield (1 ÷ P/S)", unit: "fraction" },
  book_to_price: { label: "Book to price (1 ÷ P/B)", unit: "fraction" },
  ebitda_to_ev: { label: "EBITDA to EV", unit: "fraction" },
  fcf_yield: { label: "Free cash flow yield", unit: "fraction" },
  dividend_yield: { label: "Dividend yield", unit: "fraction" },
  buyback_yield: { label: "Buyback yield", unit: "fraction" },
  log_market_cap: { label: "Market cap", unit: "log_cap" },
  macro_10y: { label: "10-year Treasury yield", unit: "percent_points" },
  macro_curve_10y3m: { label: "Yield curve (10-year − 3-month)", unit: "percent_points" },
  macro_unemployment: { label: "Unemployment rate", unit: "percent_points" },
  macro_nfci: { label: "Financial conditions (NFCI)", unit: "ratio" },
  macro_cpi_yoy: { label: "Inflation (CPI, 12 months)", unit: "fraction" },
  macro_unemployment_change_3m: { label: "Unemployment change (3 months)", unit: "percent_points" },
  macro_10y_change_3m: { label: "10-year yield change (3 months)", unit: "percent_points" },
  market_trend: { label: "S&P 500 vs its 200-day average", unit: "fraction" },
  market_vol_1m: { label: "S&P 500 1-month volatility", unit: "fraction" },
};

export function featureLabel(key: string): string {
  return FEATURES[key]?.label ?? key.replaceAll("_", " ");
}

export function formatFeature(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  switch (FEATURES[key]?.unit) {
    case "fraction":
      return formatValue(value, "percent");
    case "percent_points":
      return `${value.toFixed(2)}%`;
    case "index":
      return value.toFixed(0);
    case "log_cap":
      return formatValue(Math.exp(value), "currency");
    default:
      return value.toFixed(2);
  }
}

/** "+1.23%" for a fraction; "—" when missing. */
export function signedPct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : formatPercent(value * 100, digits);
}

export function percentileLabel(percentile: number): string {
  const p = Math.round(percentile * 100);
  const suffix = p % 10 === 1 && p !== 11 ? "st" : p % 10 === 2 && p !== 12 ? "nd" : p % 10 === 3 && p !== 13 ? "rd" : "th";
  return `${p}${suffix} percentile`;
}

export const OWNERSHIP_CHANGES: Record<string, string> = {
  increased: "Added",
  decreased: "Reduced",
  unchanged: "Unchanged",
  entered_top: "New to top 100",
  left_top: "Left top 100",
  unknown: "—",
};

/** Probability shown as a percentage with one decimal. */
export function probability(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** Thin a long series to about `target` points, always keeping the last. */
export function thin<T>(points: T[], target = 500): T[] {
  if (points.length <= target) return points;
  const step = Math.ceil(points.length / target);
  const out = points.filter((_, i) => i % step === 0);
  if (out.at(-1) !== points.at(-1)) out.push(points.at(-1)!);
  return out;
}
