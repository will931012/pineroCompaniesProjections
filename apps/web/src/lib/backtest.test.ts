import { describe, expect, it } from "vitest";
import type { BacktestOptions } from "./api/endpoints";
import { annualTable, defaultForm, formatStat, toRequest } from "./backtest";

const OPTIONS: BacktestOptions = {
  factors: [],
  first_signal: "2012-01-31",
  last_signal: "2026-09-30",
  universe_size: 200,
  costs: [{ key: "commission_per_share", label: "Commission", value: 0.005, explanation: "" }],
  notes: [],
};

describe("backtest form", () => {
  it("builds a request from the defaults", () => {
    const { request, error } = toRequest(defaultForm(OPTIONS));
    expect(error).toBeNull();
    expect(request?.factors).toEqual([{ factor: "quality", weight: 1 }]);
    expect(request?.start).toBe("2012-01-31");
    expect(request?.end).toBeNull();
    expect(request?.costs).toEqual({ commission_per_share: 0.005 });
  });

  it("rejects forms that cannot run", () => {
    const form = defaultForm(OPTIONS);
    expect(toRequest({ ...form, weights: { ...form.weights, quality: 0 } }).error).toBe("Choose at least one factor.");
    expect(toRequest({ ...form, start: "2020-01-01", end: "2019-01-01" }).error).toBe("The start must be before the end.");
  });

  it("formats statistics and annual comparisons", () => {
    expect(formatStat(0.1492, "percent")).toBe("14.9%");
    expect(formatStat(0.849, "ratio")).toBe("0.85");
    expect(formatStat(0.9993, "probability")).toBe("100%");
    expect(formatStat(null, "ratio")).toBe("—");
    expect(annualTable({ "2022": -0.02, "2023": 0.15 }, { "2022": -0.18 })).toEqual([
      ["2022", -0.02, -0.18, -0.02 + 0.18],
      ["2023", 0.15, null, null],
    ]);
  });
});
