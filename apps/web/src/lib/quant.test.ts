import { describe, expect, it } from "vitest";
import { featureLabel, formatFeature, percentileLabel, probability, signedPct, thin } from "./quant";

describe("quant formatting", () => {
  it("labels and formats model inputs by unit", () => {
    expect(featureLabel("mom_12_1")).toBe("12-month return, excluding the last month");
    expect(featureLabel("unknown_thing")).toBe("unknown thing");
    expect(formatFeature("gross_margin", 0.4512)).toBe("45.1%");
    expect(formatFeature("macro_10y", 4.237)).toBe("4.24%");
    expect(formatFeature("rsi_14", 71.6)).toBe("72");
    expect(formatFeature("beta_1y", 1.234)).toBe("1.23");
    expect(formatFeature("log_market_cap", Math.log(2.5e12))).toBe("$2.50T");
    expect(formatFeature("roe", null)).toBe("—");
  });

  it("writes percentiles and probabilities", () => {
    expect(percentileLabel(0.91)).toBe("91st percentile");
    expect(percentileLabel(0.12)).toBe("12th percentile");
    expect(percentileLabel(0.53)).toBe("53rd percentile");
    expect(probability(0.5523)).toBe("55.2%");
    expect(signedPct(-0.0123)).toBe("−1.2%");
    expect(signedPct(null)).toBe("—");
  });

  it("thins long series but keeps the last point", () => {
    const points = Array.from({ length: 1001 }, (_, i) => i);
    const out = thin(points, 100);
    expect(out.length).toBeLessThanOrEqual(102);
    expect(out.at(-1)).toBe(1000);
    expect(thin([1, 2, 3], 100)).toEqual([1, 2, 3]);
  });
});
