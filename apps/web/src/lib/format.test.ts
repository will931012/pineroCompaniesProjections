import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatFiscalYearEnd,
  formatPercent,
  formatPrice,
  formatSignedNumber,
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
