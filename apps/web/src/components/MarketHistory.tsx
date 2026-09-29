"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { isApiError } from "@/lib/api/client";
import { api, type MarketBarsResponse } from "@/lib/api/endpoints";
import { isoDate, shiftIsoDate } from "@/lib/format";
import { RANGE_DAYS, usePreferences, type ChartRange } from "@/stores/preferences";
import { PriceChart } from "./PriceChart";
import { ErrorNotice, Panel, SourceList, StatusPill } from "./ui";

export function useDailyBars(ticker: string, range: ChartRange) {
  // Anchor the window to the UTC date at mount so the query key is stable.
  const [to] = useState(() => isoDate(new Date()));
  const from = shiftIsoDate(to, -RANGE_DAYS[range]);
  return useQuery({
    queryKey: ["bars", ticker, from, to],
    queryFn: () => api.dailyBars(ticker, from, to),
    staleTime: 5 * 60_000,
  });
}

const RANGES: ChartRange[] = ["1M", "6M", "1Y", "5Y"];

export function MarketHistory({ ticker }: { ticker: string }) {
  const { chartRange, setChartRange, priceBasis, setPriceBasis } = usePreferences();
  const bars = useDailyBars(ticker, chartRange);
  const data: MarketBarsResponse | undefined = bars.data;

  const controls = (
    <div className="chart-controls">
      <div className="segmented" role="group" aria-label="Range">
        {RANGES.map((range) => (
          <button aria-pressed={range === chartRange} key={range} onClick={() => setChartRange(range)}
            type="button">{range}</button>
        ))}
      </div>
      <div className="segmented" role="group" aria-label="Price basis">
        {(["adjusted", "raw"] as const).map((basis) => (
          <button aria-pressed={basis === priceBasis} key={basis} onClick={() => setPriceBasis(basis)}
            type="button" title={basis === "adjusted" ? "Provider split/dividend-adjusted" : "As traded"}>
            {basis === "adjusted" ? "Adj." : "Raw"}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <Panel kicker="MARKET HISTORY · DAILY" title="Price performance" action={controls} className="chart-panel">
      {bars.isPending && <div className="chart-placeholder">Requesting verified price history…</div>}
      {bars.isError && (
        isApiError(bars.error) && bars.error.status === 503 ? (
          <div className="chart-placeholder">
            <strong>Price history unavailable</strong>
            <span>{bars.error.message}</span>
          </div>
        ) : <ErrorNotice error={bars.error} title="Price history unavailable" />
      )}
      {data && data.bars.length === 0 && (
        <div className="chart-placeholder"><strong>No bars in this range</strong>
          <span>The provider returned no trading days for the requested window.</span></div>
      )}
      {data && data.bars.length > 0 && (
        <>
          <PriceChart bars={data.bars} basis={priceBasis} />
          <div className="chart-footer">
            <StatusPill tone={data.quality.status === "stale" ? "warn" : "ok"}>
              {data.quality.status.toUpperCase()}
            </StatusPill>
            <span>{data.bars.length} sessions · {data.provider}</span>
          </div>
          {data.quality.warnings.map((warning) => (
            <p className="quality-warning" key={warning}>{warning}</p>
          ))}
          <details className="sources">
            <summary>Sources ({data.sources.length})</summary>
            <SourceList sources={data.sources} />
          </details>
        </>
      )}
    </Panel>
  );
}
