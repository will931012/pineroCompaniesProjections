import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatFiscalYearEnd,
  formatPercent,
  formatPrice,
  formatCompact,
  formatSignedNumber,
  formatValue,
  shiftIsoDate,
} from "./format";

describe("formatters never invent values", () => {
  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY])("renders %s as an em dash", (value) => {
    expect(formatPrice(value)).toBe("—");
    expect(formatPercent(value)).toBe("—");
    expect(formatSignedNumber(value)).toBe("—");
  });

  it("formats prices with precision appropriate to magnitude", () => {
    expect(formatPrice(1234.5)).toBe("1,234.50");
    expect(formatPrice(0.12345)).toBe("0.1235");
  });

  it("signs changes explicitly", () => {
    expect(formatSignedNumber(2)).toBe("+2.00");
    expect(formatSignedNumber(-2)).toBe("−2.00");
    expect(formatPercent(0)).toBe("0.00%");
  });
});

describe("dates", () => {
  it("treats YYYY-MM-DD as a calendar date without timezone drift", () => {
    expect(formatDate("2026-01-02")).toBe("Jan 2, 2026");
  });

  it("shifts calendar dates across month and year boundaries", () => {
    expect(shiftIsoDate("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftIsoDate("2026-01-01", -183)).toBe("2025-07-02");
  });

  it("formats SEC fiscal year end codes", () => {
    expect(formatFiscalYearEnd("0926")).toBe("Sep 26");
    expect(formatFiscalYearEnd("1331")).toBe("—");
    expect(formatFiscalYearEnd(null)).toBe("—");
  });
});

describe("formatValue", () => {
  it("formats by unit", () => {
    expect(formatValue(0.1534, "percent")).toBe("15.3%");
    expect(formatValue(1.5, "ratio")).toBe("1.50");
    expect(formatValue(18.25, "multiple")).toBe("18.3×");
    expect(formatValue(4.2e9, "USD")).toBe("$4.20B");
    expect(formatValue(-1.5e6, "currency")).toBe("−$1.50M");
    expect(formatValue(2.5, "USD/shares")).toBe("$2.50");
    expect(formatValue(15.2e9, "shares")).toBe("15.20B");
  });

  it("never invents a value", () => {
    expect(formatValue(null, "percent")).toBe("—");
    expect(formatValue(Number.NaN, "USD")).toBe("—");
  });

  it("compacts small numbers without suffixes", () => {
    expect(formatCompact(950)).toBe("950");
    expect(formatCompact(12.345)).toBe("12.35");
  });
});
