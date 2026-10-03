"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Sparkline, YieldCurveChart } from "@/components/MiniCharts";
import { ErrorNotice, PageHeading, Panel, StatusPill } from "@/components/ui";
import { api, type MarketsOverview } from "@/lib/api/endpoints";
import { formatDate, formatPrice, formatValue } from "@/lib/format";
import { signedPct } from "@/lib/quant";

const RETURN_COLUMNS: [string, string][] = [["1d", "1D"], ["1w", "1W"], ["1m", "1M"], ["3m", "3M"], ["ytd", "YTD"], ["1y", "1Y"]];
const REGIMES = ["Uptrend, calm", "Uptrend, stressed", "Downtrend, calm", "Downtrend, stressed"];
const GROUPS: [string, string][] = [["index", "Indexes"], ["sector", "S&P 500 sectors"], ["other", "Gold and Treasuries"]];

export default function MarketsPage() {
  const query = useQuery({ queryKey: ["markets"], queryFn: api.marketsOverview });
  const data = query.data;
  return (
    <>
      <PageHeading kicker="MARKETS" title="Markets"
        subtitle="Indexes and sectors through ETFs, the Treasury curve, macro data as published, and the current market regime." />
      {query.isPending && <div className="chart-placeholder">Loading markets…</div>}
      {query.isError && <ErrorNotice error={query.error} title="Markets unavailable" />}
      {data && (
        <div className="markets-layout">
          <div className="markets-main">
            {GROUPS.map(([group, title]) => (
              <EtfTable data={data} group={group} key={group} title={title} />
            ))}
            <MacroPanel data={data} />
          </div>
          <div className="markets-side">
            <RegimePanel data={data} />
            {data.curve && (
              <Panel kicker={`U.S. TREASURY · ${formatDate(data.curve.observed_on).toUpperCase()}`} title="Yield curve">
                <YieldCurveChart curves={[
                  { name: formatDate(data.curve.observed_on), points: data.curve.points },
                  ...(data.curve_year_ago ? [{ name: formatDate(data.curve_year_ago.observed_on), points: data.curve_year_ago.points }] : []),
                ]} />
              </Panel>
            )}
            {data.bitcoin && (
              <Panel kicker={`BITCOIN · ${formatDate(data.bitcoin.as_of).toUpperCase()}`} title="Bitcoin" action={<Link href="/bitcoin">Full analysis</Link>}>
                <dl className="stat-grid compact">
                  <div><dt>Close (USD)</dt><dd>{formatPrice(data.bitcoin.close)}</dd></div>
                  <div><dt>1 day</dt><dd>{signedPct(data.bitcoin.change_1d)}</dd></div>
                  <div><dt>30 days</dt><dd>{signedPct(data.bitcoin.change_30d)}</dd></div>
                  <div><dt>Below its high</dt><dd>{signedPct(data.bitcoin.drawdown)}</dd></div>
                </dl>
              </Panel>
            )}
          </div>
          <ul className="source-notes">{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
    </>
  );
}

function EtfTable({ data, group, title }: { data: MarketsOverview; group: string; title: string }) {
  const rows = data.etfs.filter((e) => e.group === group);
  return (
    <Panel kicker="TOTAL RETURN · DIVIDENDS INCLUDED" title={title}>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Fund</th><th className="num">Close</th>
              {RETURN_COLUMNS.map(([, label]) => <th className="num" key={label}>{label}</th>)}
              <th>1 year</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.ticker}>
                <td><b>{e.ticker}</b><small>{e.name}</small></td>
                <td className="num">{e.close === null ? "not loaded" : formatPrice(e.close)}</td>
                {RETURN_COLUMNS.map(([key]) => {
                  const value = e.returns[key];
                  return <td className={`num ${value && value < 0 ? "neg" : ""}`} key={key}>{signedPct(value)}</td>;
                })}
                <td><Sparkline label={`${e.ticker} over one year`} values={e.spark.map((p) => p.value)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function RegimePanel({ data }: { data: MarketsOverview }) {
  const regime = data.regime;
  if (!regime) {
    return <Panel kicker="REGIME" title="Not available"><p className="panel-copy">Needs about a year of S&amp;P 500 (SPY) prices.</p></Panel>;
  }
  const c = regime.components as Record<string, number | boolean | null>;
  const history = regime.history.slice(-24);
  return (
    <Panel kicker={`MARKET REGIME · ${formatDate(regime.as_of).toUpperCase()}`} title={regime.label}>
      <dl className="stat-grid compact">
        <div><dt>S&amp;P 500 vs 200-day avg</dt><dd>{signedPct(Number(c.spy_close) / Number(c.spy_sma200) - 1)}</dd></div>
        <div><dt>21-day volatility</dt><dd>{formatValue(c.vol_21d as number, "percent")}</dd></div>
        <div><dt>Volatility percentile (10y)</dt><dd>{formatValue(c.vol_percentile_10y as number, "percent")}</dd></div>
        <div><dt>Curve 10y − 3m</dt><dd>{c.curve_10y3m === null ? "needs FRED" : `${Number(c.curve_10y3m).toFixed(2)} pts`}</dd></div>
      </dl>
      <div className="flag-row">
        {c.curve_inverted && <StatusPill tone="warn">CURVE INVERTED</StatusPill>}
        {c.tight_conditions && <StatusPill tone="warn">TIGHT FINANCIAL CONDITIONS</StatusPill>}
      </div>
      <p className="panel-copy">
        Rules: trend is SPY above or below its 200-day average; &quot;stressed&quot; means 21-day volatility above the 80th
        percentile of the last ten years. Flags use FRED&apos;s 10y−3m spread and the Chicago Fed NFCI.
      </p>
      {history.length > 0 && (
        <ol className="regime-strip" aria-label="Regime at recent month ends">
          {history.map(([d, label]) => (
            <li className={`regime-${label.toLowerCase().replace(", ", "-")}`} key={d} title={`${formatDate(d)}: ${label}`}>
              <span>{d.slice(2, 7)}</span>
            </li>
          ))}
        </ol>
      )}
      {history.length > 0 && (
        <div className="chart-legend regime-legend">
          {REGIMES.map((label) => (
            <span key={label}><i className={`legend-${label.toLowerCase().replace(", ", "-")}`} />{label}</span>
          ))}
        </div>
      )}
    </Panel>
  );
}

function MacroPanel({ data }: { data: MarketsOverview }) {
  if (!data.macro_configured || data.macro.every((m) => !m.available)) {
    return (
      <Panel kicker="MACRO · FRED" title="Macro data not loaded">
        <p className="panel-copy">
          {data.macro_configured
            ? "The worker loads FRED series daily; none are stored yet."
            : "Set FRED_API_KEY (free from fredaccount.stlouisfed.org) to load rates, inflation, employment, and financial conditions."}
        </p>
      </Panel>
    );
  }
  return (
    <Panel kicker="MACRO · AS PUBLISHED" title="Economy and rates">
      <div className="macro-grid">
        {data.macro.filter((m) => m.available).map((m) => (
          <section className="macro-tile" key={m.series_id}>
            <span className="section-kicker">{m.series_id}</span>
            <b>{m.label}</b>
            <strong>{m.unit === "percent" ? `${m.value?.toFixed(2)}%` : m.unit === "thousands" ? formatValue((m.value ?? 0) * 1000, "shares") : formatValue(m.value, "ratio")}</strong>
            <small>
              {formatDate(m.observation_date)} · published {formatDate(m.available_on)}
              {m.yoy_change !== null && m.yoy_change !== undefined
                ? ` · 1y ${m.unit === "percent" ? `${m.yoy_change >= 0 ? "+" : "−"}${Math.abs(m.yoy_change).toFixed(2)} pts` : signedPct(m.yoy_change)}`
                : ""}
            </small>
            <Sparkline label={`${m.label}, last five years`} values={m.history.map((p) => p.value)} width={150} />
            <small>{m.source}</small>
          </section>
        ))}
      </div>
    </Panel>
  );
}
