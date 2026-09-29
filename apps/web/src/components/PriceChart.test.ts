import { describe, expect, it } from "vitest";
import type { DailyBar } from "@/lib/api/endpoints";
import { COMPANY_TABS, MODULES } from "@/lib/modules";
import { toCandles } from "./PriceChart";

const bar = (overrides: Partial<DailyBar> = {}): DailyBar => ({
  date: "2026-01-02", open: 200, high: 210, low: 190, close: 205, volume: 1000,
  adj_open: 100, adj_high: 105, adj_low: 95, adj_close: 102.5, dividend_cash: 0, split_factor: 1,
  fetch_id: 1, ...overrides,
});

describe("toCandles", () => {
  it("uses the provider's adjusted values when requested", () => {
    expect(toCandles([bar()], "adjusted")[0]).toMatchObject({ open: 100, close: 102.5 });
  });

  it("uses raw values when requested", () => {
    expect(toCandles([bar()], "raw")[0]).toMatchObject({ open: 200, close: 205 });
  });

  it("falls back to raw values rather than mixing when adjustments are missing", () => {
    expect(toCandles([bar({ adj_low: null })], "adjusted")[0]).toMatchObject({ open: 200, low: 190 });
  });

  it("uses UTC midnight timestamps", () => {
    expect(toCandles([bar()], "raw")[0].time).toBe(Date.UTC(2026, 0, 2) / 1000);
  });
});

describe("module registry", () => {
  it("has unique slugs and hrefs", () => {
    expect(new Set(MODULES.map((m) => m.slug)).size).toBe(MODULES.length);
    expect(new Set(MODULES.map((m) => m.href)).size).toBe(MODULES.length);
  });

  it("describes every planned module", () => {
    for (const workspaceModule of MODULES.filter((m) => m.phase > 1)) {
      expect(workspaceModule.planned.length).toBeGreaterThan(0);
    }
  });

  it("covers every company tab required by the spec", () => {
    expect(COMPANY_TABS.map((t) => t.label)).toEqual([
      "Overview", "Financials", "Valuation", "SEC", "Earnings", "News", "Technicals", "Quant",
      "Ownership", "Insiders", "Peers", "AI Research",
    ]);
  });
});
