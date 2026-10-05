import type { BacktestIn, BacktestOptions } from "./api/endpoints";
import { formatValue } from "./format";

export type Factor = BacktestIn["factors"][number]["factor"];
export type Stats = Record<string, number | string | null>;

export type FormState = {
  name: string;
  weights: Record<Factor, number>; // 0 = not used
  selection: "top_n" | "top_quantile";
  topN: number;
  topQuantile: number;
  weighting: "equal" | "score" | "inverse_volatility";
  maxWeight: number;
  rebalance: "monthly" | "quarterly";
  start: string;
  end: string;
  capital: number;
  costs: Record<string, number>;
};

export const FACTOR_KEYS: Factor[] = ["value", "quality", "momentum", "low_volatility", "growth", "size"];

export function defaultForm(options: BacktestOptions): FormState {
  return {
    name: "Quality, top 20",
    weights: { value: 0, quality: 1, momentum: 0, low_volatility: 0, growth: 0, size: 0 },
    selection: "top_n",
    topN: 20,
    topQuantile: 0.2,
    weighting: "equal",
    maxWeight: 0.1,
    rebalance: "monthly",
    start: options.first_signal ?? "",
    end: "",
    capital: 1_000_000,
    costs: Object.fromEntries(options.costs.map((c) => [c.key, c.value])),
  };
}

/** The request for a form; null with a reason when it cannot be run. */
export function toRequest(form: FormState): { request: BacktestIn | null; error: string | null } {
  const factors = FACTOR_KEYS.filter((f) => form.weights[f] > 0).map((factor) => ({ factor, weight: form.weights[factor] }));
  if (factors.length === 0) return { request: null, error: "Choose at least one factor." };
  if (!form.name.trim()) return { request: null, error: "Name the backtest." };
  if (form.start && form.end && form.start >= form.end) return { request: null, error: "The start must be before the end." };
  return {
    error: null,
    request: {
      name: form.name.trim(),
      factors,
      selection: form.selection,
      top_n: form.topN,
      top_quantile: form.topQuantile,
      weighting: form.weighting,
      max_weight: form.maxWeight,
      rebalance: form.rebalance,
      start: form.start || null,
      end: form.end || null,
      capital: form.capital,
      costs: form.costs as BacktestIn["costs"],
    },
  };
}

type Row = { key: string; label: string; unit: "percent" | "ratio" | "days" | "probability" };

export const STAT_ROWS: Row[] = [
  { key: "cagr", label: "Annual return (CAGR)", unit: "percent" },
  { key: "total_return", label: "Total return", unit: "percent" },
  { key: "volatility", label: "Volatility", unit: "percent" },
  { key: "sharpe", label: "Sharpe ratio", unit: "ratio" },
  { key: "sortino", label: "Sortino ratio", unit: "ratio" },
  { key: "max_drawdown", label: "Largest fall", unit: "percent" },
  { key: "longest_drawdown_days", label: "Longest time below a high", unit: "days" },
  { key: "beta", label: "Beta to SPY", unit: "ratio" },
  { key: "alpha", label: "Alpha vs SPY (annual)", unit: "percent" },
  { key: "tracking_error", label: "Tracking error", unit: "percent" },
  { key: "information_ratio", label: "Information ratio", unit: "ratio" },
  { key: "psr", label: "Probability it truly beats SPY", unit: "probability" },
];

export function formatStat(value: number | string | null | undefined, unit: Row["unit"]): string {
  if (value === null || value === undefined || typeof value === "string") return value ?? "—";
  switch (unit) {
    case "percent":
      return formatValue(value, "percent");
    case "days":
      return `${Math.round(value)} days`;
    case "probability":
      return `${(value * 100).toFixed(0)}%`;
    default:
      return value.toFixed(2);
  }
}

/** Year → [strategy, SPY, difference]; years present in the strategy only. */
export function annualTable(strategy: Record<string, number>, spy: Record<string, number>): [string, number, number | null, number | null][] {
  return Object.keys(strategy).sort().map((year) => {
    const own = strategy[year];
    const bench = spy[year] ?? null;
    return [year, own, bench, bench === null ? null : own - bench];
  });
}
