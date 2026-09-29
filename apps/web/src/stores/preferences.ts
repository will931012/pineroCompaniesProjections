import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type ChartRange = "1M" | "6M" | "1Y" | "5Y";
export type PriceBasis = "adjusted" | "raw";

type Preferences = {
  chartRange: ChartRange;
  priceBasis: PriceBasis;
  setChartRange: (range: ChartRange) => void;
  setPriceBasis: (basis: PriceBasis) => void;
};

/** Per-browser display preferences. Nothing here is research data. */
export const usePreferences = create<Preferences>()(
  persist(
    (set) => ({
      chartRange: "6M",
      priceBasis: "adjusted",
      setChartRange: (chartRange) => set({ chartRange }),
      setPriceBasis: (priceBasis) => set({ priceBasis }),
    }),
    { name: "pinero-preferences", storage: createJSONStorage(() => localStorage) },
  ),
);

export const RANGE_DAYS: Record<ChartRange, number> = { "1M": 31, "6M": 183, "1Y": 366, "5Y": 1827 };
