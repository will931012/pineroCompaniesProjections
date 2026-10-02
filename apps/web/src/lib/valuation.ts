import type { Schemas } from "./api/client";

export type ValuationModel = "dcf" | "rim" | "ddm";
export type ValuationDefaults = Schemas["DefaultsOut"];
export type ValuationRequest = Schemas["ValuationIn"];

/** How a field is typed in the form: rates as percentages, amounts in millions. */
export type FieldUnit = "percent" | "points" | "millions" | "multiple" | "dollars";

export type FieldSpec = {
  /** Path in the request, e.g. "dcf.growth" or "capm.beta". */
  key: string;
  label: string;
  unit: FieldUnit;
  /** Key in the defaults' `sources` map. */
  source?: string;
  /** Blank means "use the stated default" (sent as null). */
  optional?: string;
};

export const MODEL_LABELS: Record<ValuationModel, string> = {
  dcf: "Discounted cash flow",
  rim: "Residual income",
  ddm: "Dividend discount",
};

export const MODEL_FIELDS: Record<ValuationModel, FieldSpec[]> = {
  dcf: [
    { key: "dcf.base_revenue", label: "Revenue (base year)", unit: "millions", source: "base_revenue" },
    { key: "dcf.growth", label: "Revenue growth, years 1–5", unit: "percent", source: "growth" },
    { key: "dcf.margin", label: "Operating margin, years 1–5", unit: "percent", source: "margin" },
    { key: "dcf.long_run_margin", label: "Long-run operating margin", unit: "percent", source: "long_run_margin" },
    { key: "dcf.terminal_growth", label: "Terminal growth", unit: "percent", source: "terminal_growth" },
    { key: "dcf.tax_rate", label: "Tax rate", unit: "percent", source: "tax_rate" },
    { key: "dcf.sales_to_capital", label: "Sales to capital", unit: "multiple", source: "sales_to_capital" },
    { key: "dcf.terminal_roic", label: "Return on new capital", unit: "percent", source: "terminal_roic",
      optional: "equal to the discount rate" },
    { key: "dcf.debt", label: "Debt", unit: "millions", source: "debt" },
    { key: "dcf.cash", label: "Cash and investments", unit: "millions", source: "cash" },
    { key: "dcf.shares", label: "Shares (millions)", unit: "millions", source: "shares" },
  ],
  rim: [
    { key: "rim.book_value", label: "Book equity", unit: "millions", source: "book_value" },
    { key: "rim.roe", label: "Return on equity, years 1–5", unit: "percent", source: "roe" },
    { key: "rim.long_run_roe", label: "Long-run return on equity", unit: "percent", source: "long_run_roe",
      optional: "equal to the cost of equity" },
    { key: "rim.payout_ratio", label: "Payout ratio", unit: "percent", source: "payout_ratio" },
    { key: "rim.terminal_growth", label: "Terminal growth", unit: "percent", source: "terminal_growth" },
    { key: "rim.shares", label: "Shares (millions)", unit: "millions", source: "shares" },
  ],
  ddm: [
    { key: "ddm.dividend_per_share", label: "Dividend per share", unit: "dollars", source: "dividend_per_share" },
    { key: "ddm.growth", label: "Dividend growth, years 1–5", unit: "percent", source: "dividend_growth" },
    { key: "ddm.terminal_growth", label: "Terminal growth", unit: "percent", source: "terminal_growth" },
  ],
};

export const CAPM_FIELDS: FieldSpec[] = [
  { key: "capm.risk_free", label: "Risk-free rate", unit: "percent", source: "risk_free" },
  { key: "capm.beta", label: "Beta", unit: "multiple", source: "beta" },
  { key: "capm.equity_risk_premium", label: "Equity risk premium", unit: "percent", source: "equity_risk_premium" },
  { key: "capm.pre_tax_cost_of_debt", label: "Pre-tax cost of debt", unit: "percent", source: "pre_tax_cost_of_debt" },
  { key: "capm.equity_value", label: "Equity value (weight)", unit: "millions", source: "equity_value" },
  { key: "capm.debt_value", label: "Debt value (weight)", unit: "millions", source: "debt_value" },
];

export const OVERRIDE_FIELD: FieldSpec = {
  key: "discount_rate_override",
  label: "Discount rate override",
  unit: "percent",
  optional: "computed from CAPM",
};

export const SCENARIO_SHIFTS: { key: "growth" | "margin" | "discount_rate" | "terminal_growth"; label: string }[] = [
  { key: "growth", label: "Growth" },
  { key: "margin", label: "Margin / ROE" },
  { key: "discount_rate", label: "Discount rate" },
  { key: "terminal_growth", label: "Terminal growth" },
];

export const scenarioFields = (name: "bear" | "bull"): FieldSpec[] =>
  SCENARIO_SHIFTS.map(({ key, label }) => ({ key: `scenarios.${name}.${key}`, label, unit: "points" }));

/** Form values are kept as the strings the user typed, keyed by request path. */
export type FormState = Record<string, string>;

const SCALE: Record<FieldUnit, number> = { percent: 100, points: 100, millions: 1e-6, multiple: 1, dollars: 1 };

function trim(value: number): string {
  return String(Number(value.toPrecision(12)));
}

export function toInput(value: number | null | undefined, unit: FieldUnit): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  const scaled = value * SCALE[unit];
  return trim(unit === "millions" ? Number(scaled.toFixed(3)) : Number(scaled.toFixed(4)));
}

/** null for blank, undefined for text that is not a number. */
export function fromInput(text: string, unit: FieldUnit): number | null | undefined {
  const cleaned = text.replaceAll(",", "").replace("%", "").trim();
  if (cleaned === "") return null;
  const parsed = Number(cleaned.replace("−", "-"));
  if (!Number.isFinite(parsed)) return undefined;
  return Number((parsed / SCALE[unit]).toPrecision(12));
}

function read(source: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>(
    (node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined),
    source,
  );
}

export function allFields(): FieldSpec[] {
  return [
    ...MODEL_FIELDS.dcf, ...MODEL_FIELDS.rim, ...MODEL_FIELDS.ddm, ...CAPM_FIELDS, OVERRIDE_FIELD,
    ...scenarioFields("bear"), ...scenarioFields("bull"),
  ];
}

/** Form state from defaults or a saved request (same shape for the fields used here). */
export function formFrom(source: Partial<ValuationRequest> | ValuationDefaults): FormState {
  const state: FormState = {};
  for (const field of allFields()) {
    const value = read(source, field.key);
    state[field.key] = typeof value === "number" ? toInput(value, field.unit) : "";
  }
  state["dcf.mid_year"] = read(source, "dcf.mid_year") === true ? "true" : "false";
  return state;
}

export type BuiltRequest = { request: ValuationRequest | null; errors: Record<string, string> };

/** Turn the form into a request for one model; fields of other models are left out. */
export function buildRequest(
  model: ValuationModel,
  form: FormState,
  sources: Record<string, string>,
  defaults: FormState,
): BuiltRequest {
  const errors: Record<string, string> = {};
  const used = [...MODEL_FIELDS[model], ...CAPM_FIELDS, OVERRIDE_FIELD, ...scenarioFields("bear"), ...scenarioFields("bull")];
  const body: Record<string, unknown> = { model };
  const recorded: Record<string, string> = {};
  for (const field of used) {
    const text = form[field.key] ?? "";
    const value = fromInput(text, field.unit);
    if (value === undefined) {
      errors[field.key] = "Not a number";
      continue;
    }
    if (value === null && !field.optional && !field.key.startsWith("scenarios.")) {
      errors[field.key] = "Required";
      continue;
    }
    const parts = field.key.split(".");
    let node = body;
    for (const part of parts.slice(0, -1)) {
      node[part] = (node[part] as Record<string, unknown>) ?? {};
      node = node[part] as Record<string, unknown>;
    }
    node[parts.at(-1)!] = value ?? (field.key.startsWith("scenarios.") ? 0 : null);
    if (field.source) {
      const original = sources[field.source];
      const edited = text.trim() !== (defaults[field.key] ?? "").trim();
      if (edited) {
        recorded[field.source] = `edited${defaults[field.key] ? ` (default ${defaults[field.key]}${unitSuffix(field.unit)})` : ""}`;
      } else if (original) {
        recorded[field.source] = original;
      }
    }
  }
  if (model === "dcf") (body.dcf as Record<string, unknown>).mid_year = form["dcf.mid_year"] === "true";
  body.sources = recorded;
  if (Object.keys(errors).length) return { request: null, errors };
  return { request: body as ValuationRequest, errors };
}

export function unitSuffix(unit: FieldUnit): string {
  return unit === "percent" ? "%" : unit === "points" ? " pts" : unit === "millions" ? "M" : unit === "multiple" ? "×" : "";
}

/** Heatmap tone for a per-share value: diverging around the price, else by position in the grid. */
export type HeatTone = "below-2" | "below-1" | "near" | "above-1" | "above-2" | "low" | "mid" | "high" | "none";

export function heatTone(value: number | null, price: number | null | undefined, range: [number, number]): HeatTone {
  if (value === null || !Number.isFinite(value)) return "none";
  if (price && price > 0) {
    const upside = value / price - 1;
    if (upside <= -0.3) return "below-2";
    if (upside <= -0.1) return "below-1";
    if (upside < 0.1) return "near";
    if (upside < 0.3) return "above-1";
    return "above-2";
  }
  const [low, high] = range;
  if (high <= low) return "mid";
  const position = (value - low) / (high - low);
  return position < 1 / 3 ? "low" : position < 2 / 3 ? "mid" : "high";
}

export function gridRange(values: (number | null)[][]): [number, number] {
  const finite = values.flat().filter((v): v is number => v !== null && Number.isFinite(v));
  return finite.length ? [Math.min(...finite), Math.max(...finite)] : [0, 0];
}

export const DRIVER_LABELS: Record<string, string> = {
  discount_rate: "Discount rate",
  terminal_growth: "Terminal growth",
  growth: "Growth (yrs 1–5)",
  margin: "Margin / ROE",
};
