"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type FinancialPeriod, type FundamentalsResponse, type LineItem, type MetricSeries } from "@/lib/api/endpoints";
import { formatDate, formatValue, type ValueUnit } from "@/lib/format";
import { ColumnChart, type ChartPoint } from "./ColumnChart";
import { ErrorNotice, Panel, SourceList, StatusPill, type Tone } from "./ui";

type PeriodType = "annual" | "quarterly";

export const FUNDAMENTALS_STATUS: Record<FundamentalsResponse["status"], { tone: Tone; label: string }> = {
  current: { tone: "ok", label: "SEC XBRL CURRENT" },
  stale: { tone: "warn", label: "SEC XBRL STALE" },
  unavailable: { tone: "warn", label: "SEC XBRL UNAVAILABLE" },
  not_configured: { tone: "off", label: "SEC NOT CONFIGURED" },
  not_applicable: { tone: "off", label: "NO SEC CIK" },
  not_available: { tone: "off", label: "NO XBRL FINANCIALS" },
};

export function useFundamentals(ticker: string, period: PeriodType = "annual", asOf = "") {
  return useQuery({
    queryKey: ["fundamentals", ticker, period, asOf],
    queryFn: () => api.fundamentals(ticker, period, asOf),
    staleTime: 10 * 60_000,
  });
}

/** Charted series: statement line items or computed metrics, whichever the key names. */
const CHARTS: { title: string; source: "item" | "metric"; key: string }[] = [
  { title: "Revenue", source: "item", key: "revenue" },
  { title: "Net income", source: "item", key: "net_income" },
  { title: "Free cash flow", source: "metric", key: "free_cash_flow" },
  { title: "Operating margin", source: "metric", key: "operating_margin" },
  { title: "Return on invested capital", source: "metric", key: "roic" },
  { title: "Diluted EPS", source: "item", key: "eps_diluted" },
];

const STATEMENTS: { key: keyof FundamentalsResponse["statements"]; title: string }[] = [
  { key: "income", title: "Income statement" },
  { key: "balance", title: "Balance sheet" },
  { key: "cash_flow", title: "Cash flow statement" },
];

export function chartPoints(data: FundamentalsResponse, source: "item" | "metric", key: string): {
  points: ChartPoint[];
  unit: ValueUnit;
} | null {
  const series = source === "item"
    ? data.statements.income.concat(data.statements.balance, data.statements.cash_flow).find((i) => i.key === key)
    : data.metrics.find((m) => m.key === key);
  if (!series) return null;
  const values = new Map(
    "cells" in series
      ? series.cells.map((c) => [c.period_key, c.value])
      : series.values.map((v) => [v.period_key, v.value]),
  );
  const points = data.periods
    .filter((p) => values.has(p.key))
    .map((p) => ({ key: p.key, label: p.label, value: values.get(p.key)! }));
  return points.length ? { points, unit: series.unit } : null;
}

function cellTitle(cell: LineItem["cells"][number]): string {
  return [
    cell.derivation ? (cell.derivation.startsWith("Adjusted") ? cell.derivation : `Derived: ${cell.derivation}`) : null,
    cell.concept ? `XBRL ${cell.concept}` : null,
    cell.accession ? `Accession ${cell.accession}` : null,
    `Public ${formatDate(cell.filed_date)}`,
  ].filter(Boolean).join("\n");
}

function StatementTable({ items, periods }: { items: LineItem[]; periods: FinancialPeriod[] }) {
  if (items.length === 0) return <p className="panel-copy">No values reported for these periods.</p>;
  return (
    <div className="table-wrap">
      <table className="data-table statement-table">
        <thead>
          <tr><th>Line item</th>{periods.map((p) => <th className="num" key={p.key} title={`${p.start} → ${p.end}`}>{p.label}</th>)}</tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const cells = new Map(item.cells.map((c) => [c.period_key, c]));
            return (
              <tr key={item.key}>
                <td>{item.label}</td>
                {periods.map((p) => {
                  const cell = cells.get(p.key);
                  return (
                    <td className={`num${cell?.derivation ? " derived" : ""}`} key={p.key} title={cell ? cellTitle(cell) : "Not reported"}>
                      {formatValue(cell?.value, item.unit)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MetricTable({ metrics, periods }: { metrics: MetricSeries[]; periods: FinancialPeriod[] }) {
  const categories = [...new Set(metrics.map((m) => m.category))];
  return (
    <div className="table-wrap">
      <table className="data-table statement-table">
        <thead>
          <tr><th>Metric</th>{periods.map((p) => <th className="num" key={p.key}>{p.label}</th>)}</tr>
        </thead>
        {categories.map((category) => (
          <tbody key={category}>
            <tr className="table-group"><th colSpan={periods.length + 1}>{category}</th></tr>
            {metrics.filter((m) => m.category === category).map((metric) => {
              const values = new Map(metric.values.map((v) => [v.period_key, v.value]));
              return (
                <tr key={metric.key}>
                  <td title={metric.formula}>{metric.label}<small>{metric.formula}</small></td>
                  {periods.map((p) => <td className="num" key={p.key}>{formatValue(values.get(p.key), metric.unit)}</td>)}
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}

export function FinancialsTab({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useState<PeriodType>("annual");
  const [asOf, setAsOf] = useState("");
  const query = useFundamentals(ticker, period, asOf);
  const data = query.data;
  const status = data ? FUNDAMENTALS_STATUS[data.status] : null;

  const controls = (
    <div className="chart-controls">
      <div className="segmented" role="group" aria-label="Period">
        {(["annual", "quarterly"] as const).map((value) => (
          <button aria-pressed={value === period} key={value} onClick={() => setPeriod(value)} type="button">
            {value === "annual" ? "Annual" : "Quarterly"}
          </button>
        ))}
      </div>
      <label className="as-of-control" title="Rebuild statements from filings public on or before this date">
        <span>As of</span>
        <input type="date" value={asOf} max={new Date().toISOString().slice(0, 10)}
          onChange={(event) => setAsOf(event.target.value)} />
        {asOf && <button className="button-link" onClick={() => setAsOf("")} type="button">Latest</button>}
      </label>
    </div>
  );

  return (
    <div className={`financials${query.isFetching && data ? " is-refetching" : ""}`}>
      <Panel kicker={`SEC XBRL · ${period.toUpperCase()}${asOf ? ` · AS OF ${asOf}` : ""}`} title="Financial statements" action={controls}>
        {query.isPending && <div className="chart-placeholder">Loading SEC financial data…</div>}
        {query.isError && <ErrorNotice error={query.error} title="Financial data unavailable" />}
        {data && status && (
          <div className="chart-footer">
            <StatusPill tone={status.tone}>{status.label}</StatusPill>
            <span>{data.periods.length} periods · mapping {data.mapping_version} · formulas {data.formula_version}</span>
          </div>
        )}
        {data?.message && <p className="quality-warning">{data.message}</p>}
        {data && data.periods.length === 0 && (
          <div className="chart-placeholder">
            <strong>No {period} periods</strong>
            <span>{asOf
              ? "No periodic filing with financial data was public on or before this date."
              : period === "quarterly"
                ? "Quarters appear only when the filings report each interim period."
                : "SEC has no annual XBRL financial data stored for this company."}</span>
          </div>
        )}
        {data && data.periods.length > 0 && (
          <p className="panel-copy">
            Values are as filed with the SEC. Italic cells are derived (for example Q4 = fiscal year − nine-month
            year-to-date) or restated onto the current share basis after a stock split; hover any cell for its XBRL concept, accession number, and the date it became public.
          </p>
        )}
      </Panel>

      {data && data.periods.length > 0 && (
        <>
          <section className="chart-grid" aria-label="Historical charts">
            {CHARTS.map((chart) => {
              const series = chartPoints(data, chart.source, chart.key);
              return series && <ColumnChart key={chart.key} title={chart.title} points={series.points} unit={series.unit} />;
            })}
          </section>
          {STATEMENTS.map((statement) => (
            <Panel key={statement.key} kicker="AS REPORTED" title={statement.title}>
              <StatementTable items={data.statements[statement.key]} periods={data.periods} />
            </Panel>
          ))}
          <Panel kicker={`DETERMINISTIC · FORMULAS ${data.formula_version}`} title="Ratios and metrics">
            {data.metrics.length
              ? <MetricTable metrics={data.metrics} periods={data.periods} />
              : <p className="panel-copy">Not enough reported values to compute any metric.</p>}
            <p className="panel-copy">
              A blank cell means the inputs were not reported or the ratio is undefined (for example a non-positive
              denominator). Nothing is estimated or filled in.
            </p>
          </Panel>
          <Panel kicker="PROVENANCE" title="Sources">
            <SourceList sources={data.sources} />
          </Panel>
        </>
      )}
    </div>
  );
}
