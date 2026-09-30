import { describe, expect, it } from "vitest";
import type { FundamentalsResponse } from "@/lib/api/endpoints";
import { toBound } from "@/lib/screener";
import { barPath, niceTicks } from "./ColumnChart";
import { chartPoints } from "./FinancialsTab";

describe("column chart geometry", () => {
  it("always includes zero and covers the data with round steps", () => {
    expect(niceTicks(0, 1200)).toEqual([0, 500, 1000, 1500]);
    expect(niceTicks(-40, 90)).toEqual([-50, 0, 50, 100]);
    expect(niceTicks(0.12, 0.31)).toEqual([0, 0.2, 0.4]);
    expect(niceTicks(0, 0.3)).toEqual([0, 0.1, 0.2, 0.3]);
    expect(niceTicks(0, 0)).toEqual([0]);
  });

  it("rounds only the data end of a bar", () => {
    const up = barPath(10, 20, 100, 40);
    expect(up.startsWith("M10,100 V44")).toBe(true); // square base, radius 4 at the top
    const down = barPath(10, 20, 100, 160);
    expect(down.startsWith("M10,100 V156")).toBe(true);
    // A bar shorter than the radius never overshoots its own height.
    expect(barPath(0, 20, 100, 98)).toContain("V100");
  });
});

const response = {
  periods: [
    { key: "FY2023", label: "FY2023", fiscal_year: 2023, fiscal_quarter: null, start: "2022-10-01", end: "2023-09-30" },
    { key: "FY2024", label: "FY2024", fiscal_year: 2024, fiscal_quarter: null, start: "2023-10-01", end: "2024-09-28" },
  ],
  statements: {
    income: [{
      key: "revenue", label: "Revenue", unit: "USD",
      cells: [{ period_key: "FY2024", value: 1200, concept: "Revenues", accession: "x", filed_date: "2024-11-01", derivation: null }],
    }],
    balance: [],
    cash_flow: [],
  },
  metrics: [{
    key: "roic", label: "ROIC", category: "Returns", unit: "percent", formula: "",
    values: [{ period_key: "FY2023", value: 0.1 }, { period_key: "FY2024", value: 0.15 }],
  }],
} as unknown as FundamentalsResponse;

describe("chart series", () => {
  it("plots only periods that have a reported value, in period order", () => {
    expect(chartPoints(response, "item", "revenue")).toEqual({
      points: [{ key: "FY2024", label: "FY2024", value: 1200 }],
      unit: "USD",
    });
    expect(chartPoints(response, "metric", "roic")?.points.map((p) => p.value)).toEqual([0.1, 0.15]);
  });

  it("returns nothing rather than an empty chart", () => {
    expect(chartPoints(response, "item", "net_income")).toBeNull();
  });
});

describe("screener bounds", () => {
  it("converts percentages to fractions and ignores blanks", () => {
    expect(toBound("15", "percent")).toBeCloseTo(0.15);
    expect(toBound("2.5", "multiple")).toBe(2.5);
    expect(toBound("", "ratio")).toBeNull();
    expect(toBound("abc", "ratio")).toBeNull();
  });
});
