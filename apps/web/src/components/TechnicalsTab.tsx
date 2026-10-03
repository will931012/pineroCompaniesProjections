"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "@/lib/api/endpoints";
import { formatDate, formatPrice, formatValue } from "@/lib/format";
import { signedPct } from "@/lib/quant";
import { LineChart } from "./LineChart";
import { ErrorNotice, Panel } from "./ui";

const RANGES = [1, 3, 5, 10] as const;
const RSI_GUIDES = [{ value: 70, title: "70" }, { value: 30, title: "30" }];
const EVEN = [{ value: 1, title: "even" }];
const RETURNS: [string, string][] = [
  ["return_1w", "1 week"], ["return_1m", "1 month"], ["return_3m", "3 months"],
  ["return_ytd", "Year to date"], ["return_1y", "1 year"], ["return_3y", "3 years"],
];

export function TechnicalsTab({ ticker }: { ticker: string }) {
  const [years, setYears] = useState<(typeof RANGES)[number]>(3);
  const query = useQuery({
    queryKey: ["technicals", ticker, years],
    queryFn: () => api.technicals(ticker, years),
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  const charts = useMemo(() => {
    if (!data?.available) return null;
    const pts = data.series;
    return {
      price: [
        { name: "Close (adjusted)", points: pts.map((p) => ({ date: p.date, value: p.close })) },
        { name: "50-day average", points: pts.map((p) => ({ date: p.date, value: p.sma_50 })), width: 1 as const },
        { name: "200-day average", points: pts.map((p) => ({ date: p.date, value: p.sma_200 })), width: 1 as const },
      ],
      rsi: [{ name: "RSI (14)", points: pts.map((p) => ({ date: p.date, value: p.rsi_14 })) }],
      macd: [
        { name: "MACD (12, 26)", points: pts.map((p) => ({ date: p.date, value: p.macd })) },
        { name: "Signal (9)", points: pts.map((p) => ({ date: p.date, value: p.macd_signal })), width: 1 as const },
      ],
      relative: [{ name: "Relative to S&P 500", points: pts.map((p) => ({ date: p.date, value: p.relative })) }],
    };
  }, [data]);

  const stats = data?.stats ?? {};
  const range = (
    <div className="segmented" role="group" aria-label="Chart range">
      {RANGES.map((r) => (
        <button aria-pressed={years === r} key={r} type="button" onClick={() => setYears(r)}>{r}Y</button>
      ))}
    </div>
  );
  return (
    <div className="company-tab-body">
      {query.isError && <ErrorNotice error={query.error} title="Technicals unavailable" />}
      {query.isPending && <div className="chart-placeholder">Loading price history…</div>}
      {data && !data.available && (
        <Panel kicker="TECHNICALS" title="Not enough price history"><p className="panel-copy">{data.message}</p></Panel>
      )}
      {data?.available && charts && (
        <>
          <Panel kicker={`TECHNICALS · PRICES TO ${formatDate(data.price_date)}`} title="Price and trend" action={range}
            className={query.isFetching ? "is-refetching" : ""}>
            <dl className="stat-grid">
              {RETURNS.map(([key, label]) => (
                <div key={key}><dt>{label}</dt><dd>{signedPct(stats[key])}</dd></div>
              ))}
              <div><dt>52-week range</dt><dd>{formatPrice(stats.low_52w)} – {formatPrice(stats.high_52w)}</dd></div>
              <div><dt>vs 200-day avg</dt><dd>{signedPct(stats.dist_sma_200)}</dd></div>
            </dl>
            <LineChart label={`${ticker} adjusted close with 50- and 200-day averages`} series={charts.price} height={300} />
          </Panel>
          <div className="quant-grid">
            <Panel kicker="MOMENTUM" title="RSI (14 days)">
              <p className="panel-copy">Latest {formatValue(stats.rsi_14, "ratio")}. Above 70 is often read as overbought, below 30 as oversold.</p>
              <LineChart label="Relative strength index" series={charts.rsi} height={160}
                guides={RSI_GUIDES} />
            </Panel>
            <Panel kicker="MOMENTUM" title="MACD">
              <p className="panel-copy">The 12-day minus the 26-day exponential average, with its 9-day signal line.</p>
              <LineChart label="MACD and signal line" series={charts.macd} height={160} />
            </Panel>
            <Panel kicker="RISK" title="Volatility and beta">
              <dl className="stat-grid compact">
                <div><dt>Volatility, 1 month</dt><dd>{formatValue(stats.vol_1m, "percent")}</dd></div>
                <div><dt>Volatility, 3 months</dt><dd>{formatValue(stats.vol_3m, "percent")}</dd></div>
                <div><dt>Volatility, 1 year</dt><dd>{formatValue(stats.vol_1y, "percent")}</dd></div>
                <div><dt>Beta vs S&amp;P 500 (1 year)</dt><dd>{formatValue(stats.beta_1y, "ratio")}</dd></div>
                <div><dt>Largest fall, 1 year</dt><dd>{signedPct(stats.max_drawdown_1y)}</dd></div>
              </dl>
              <p className="panel-copy">Volatility is annualised from daily total returns.</p>
            </Panel>
            <Panel kicker="RELATIVE STRENGTH" title="Versus the S&P 500">
              <p className="panel-copy">Growth of the stock divided by growth of SPY, both with dividends, starting at 1. Rising means outperforming.</p>
              <LineChart label="Relative strength versus SPY" series={charts.relative} height={160}
                guides={EVEN} />
            </Panel>
          </div>
          <p className="panel-copy">{data.source} Indicators describe past prices; they do not predict.</p>
        </>
      )}
    </div>
  );
}
