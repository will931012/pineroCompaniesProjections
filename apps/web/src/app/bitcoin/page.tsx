"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { LineChart } from "@/components/LineChart";
import { XYChart } from "@/components/MiniCharts";
import { ErrorNotice, PageHeading, Panel } from "@/components/ui";
import { api, type BitcoinAnalysis } from "@/lib/api/endpoints";
import { formatDate, formatPrice, formatValue } from "@/lib/format";
import { signedPct, thin } from "@/lib/quant";

const RETURNS: [string, string][] = [["1d", "1 day"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"], ["ytd", "Year to date"], ["1y", "1 year"], ["3y", "3 years"]];
const RSI_GUIDES = [{ value: 70, title: "70" }, { value: 30, title: "30" }];
const ZERO = [{ value: 0, title: "0" }];
const usd = (v: number) => `$${v >= 1000 ? Math.round(v).toLocaleString("en-US") : v.toFixed(2)}`;
const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
const multiple = (v: number) => `${v.toFixed(v < 10 ? 2 : 0)}×`;

export default function BitcoinPage() {
  const query = useQuery({ queryKey: ["bitcoin"], queryFn: api.bitcoin });
  const data = query.data;
  return (
    <>
      <PageHeading kicker="CRYPTO · BITCOIN ONLY" title="Bitcoin"
        subtitle="Price, risk, halving cycles, and how Bitcoin moves with stocks, gold, and Treasury yields." />
      {query.isPending && <div className="chart-placeholder">Loading Bitcoin history…</div>}
      {query.isError && <ErrorNotice error={query.error} title="Bitcoin unavailable" />}
      {data && !data.available && (
        <Panel kicker="BITCOIN" title="No prices loaded"><p className="panel-copy">{data.notes.join(" ")}</p></Panel>
      )}
      {data?.available && <BitcoinView data={data} />}
    </>
  );
}

function BitcoinView({ data }: { data: BitcoinAnalysis }) {
  const s = data.summary as {
    as_of: string; close: number; returns: Record<string, number | null>;
    volatility: Record<string, number | null>; all_time_high: number; all_time_high_date: string;
    drawdown: number; max_drawdown_1y: number | null; sma_50: number | null; sma_200: number | null;
    above_sma_200: boolean | null; rsi_14: number | null; log_growth_per_year: number | null;
  };
  const charts = useMemo(() => {
    const series = thin(data.series, 1500);
    return {
      price: [
        { name: "Close (USD)", points: series.map((p) => ({ date: p.date, value: p.close })) },
        { name: "50-day average", points: series.map((p) => ({ date: p.date, value: p.sma_50 })), width: 1 as const },
        { name: "200-day average", points: series.map((p) => ({ date: p.date, value: p.sma_200 })), width: 1 as const },
      ],
      drawdown: [{ name: "Below all-time high", points: series.map((p) => ({ date: p.date, value: p.drawdown })) }],
      rsi: [{ name: "RSI (14)", points: data.rsi_14.map(([date, value]) => ({ date, value })) }],
      correlations: data.relationships.map((r) => ({
        name: r.name, points: r.rolling.map(([date, value]) => ({ date, value })),
      })),
    };
  }, [data]);

  return (
    <div className="company-tab-body">
      <Panel kicker={`BTC-USD · ${formatDate(s.as_of).toUpperCase()}`} title={usd(s.close)}>
        <dl className="stat-grid">
          {RETURNS.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{signedPct(s.returns[key])}</dd></div>)}
          <div><dt>All-time high</dt><dd>{usd(s.all_time_high)} <small>{formatDate(s.all_time_high_date)}</small></dd></div>
          <div><dt>Below the high</dt><dd>{signedPct(s.drawdown)}</dd></div>
          <div><dt>Volatility 30d / 1y</dt><dd>{formatValue(s.volatility["30d"], "percent")} / {formatValue(s.volatility["1y"], "percent")}</dd></div>
          <div><dt>vs 200-day average</dt><dd>{s.sma_200 ? signedPct(s.close / s.sma_200 - 1) : "—"}</dd></div>
          <div><dt>RSI (14)</dt><dd>{s.rsi_14?.toFixed(0) ?? "—"}</dd></div>
        </dl>
        <LineChart label="Bitcoin price, log scale, with 50- and 200-day averages" series={charts.price} height={340} logScale format={usd} />
        <p className="panel-copy">Log scale: equal distances are equal percentage moves. {data.source}</p>
      </Panel>

      <div className="quant-grid">
        <Panel kicker="RISK" title="Distance below the all-time high">
          <LineChart label="Bitcoin drawdown from its running high" series={charts.drawdown} height={200} format={pct} guides={ZERO} />
        </Panel>
        <Panel kicker="MOMENTUM" title="RSI (14 days), last two years">
          <LineChart label="Bitcoin relative strength index" series={charts.rsi} height={200} guides={RSI_GUIDES} />
        </Panel>
      </div>

      <Panel kicker="CROSS-ASSET · LAST 90 SHARED TRADING DAYS" title="How Bitcoin moves with other markets">
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Compared with</th><th>Measure</th><th className="num">Correlation</th><th className="num">Beta</th><th className="num">Days</th></tr></thead>
            <tbody>
              {data.relationships.map((r) => (
                <tr key={r.name}>
                  <td>{r.name}</td>
                  <td>{r.measure === "return" ? "daily returns" : "daily change in yield"}</td>
                  <td className="num">{r.correlation?.toFixed(2) ?? "—"}</td>
                  <td className="num">{r.beta === null ? "—" : r.measure === "return" ? r.beta.toFixed(2) : `${(r.beta / 100).toFixed(2)}% per bp`}</td>
                  <td className="num">{r.observations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {charts.correlations.length > 0 && (
          <LineChart label="Rolling 90-day correlation of Bitcoin with other markets" series={charts.correlations} height={220}
            guides={ZERO} format={(v) => v.toFixed(2)} />
        )}
        <p className="panel-copy">{data.notes[0]}</p>
      </Panel>

      <HalvingPanel data={data} />
      <ul className="source-notes">{data.notes.slice(1).map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  );
}

function HalvingPanel({ data }: { data: BitcoinAnalysis }) {
  const series = data.halvings.map((h) => ({
    name: `${h.halving.slice(0, 4)} halving`,
    points: h.path.map(([days, value]) => [days, value] as [number, number]),
  }));
  return (
    <Panel kicker="HALVING CYCLES" title="Price after each halving (multiple of the halving-day price)">
      <XYChart label="Bitcoin price multiple by days since each halving, log scale" series={series}
        xLabel="Days since the halving" yFormat={multiple} logY height={300}
        xTicks={[0, 365, 730, 1095, 1460]} />
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr><th>Halving</th><th className="num">Price that day</th><th className="num">1 year later</th><th className="num">Peak multiple</th><th className="num">Days to peak</th><th className="num">Fall after peak</th></tr>
          </thead>
          <tbody>
            {data.halvings.map((h) => (
              <tr key={h.halving}>
                <td>{formatDate(h.halving)}<small>{h.days_observed} days observed</small></td>
                <td className="num">{formatPrice(h.price_at_halving)}</td>
                <td className="num">{signedPct(h.return_1y)}</td>
                <td className="num">{h.peak_multiple ? multiple(h.peak_multiple) : "—"}</td>
                <td className="num">{h.days_to_peak ?? "—"}</td>
                <td className="num">{signedPct(h.drawdown_after_peak)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="panel-copy">
        Halvings cut the new-coin reward in half every 210,000 blocks. Four cycles are too few to estimate what the next one will do. {data.next_halving_note}
      </p>
    </Panel>
  );
}
