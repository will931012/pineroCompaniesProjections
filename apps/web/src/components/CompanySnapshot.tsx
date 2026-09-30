"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type SnapshotMetric } from "@/lib/api/endpoints";
import { formatDate, formatDateTime, formatValue } from "@/lib/format";
import { useFundamentals } from "./FinancialsTab";
import { Panel } from "./ui";

/**
 * Latest stored metrics. They are recomputed whenever SEC facts or prices are refreshed, so
 * the query waits for the fundamentals request (which refreshes facts) and prices to settle.
 */
export function useCompanyMetrics(ticker: string, prices: { isPending: boolean; dataUpdatedAt: number }) {
  const fundamentals = useFundamentals(ticker);
  return useQuery({
    queryKey: ["metrics", ticker, fundamentals.dataUpdatedAt, prices.dataUpdatedAt],
    queryFn: () => api.companyMetrics(ticker),
    enabled: !fundamentals.isPending && !prices.isPending,
    placeholderData: (previous) => previous,
  });
}

export function metricTitle(metric: SnapshotMetric): string {
  return [
    metric.description,
    `Basis: ${metric.basis}`,
    metric.available_date ? `Inputs public by ${formatDate(metric.available_date)}` : null,
  ].filter(Boolean).join("\n");
}

const PANEL_ORDER = ["Valuation", "Size", "Growth", "Profitability", "Returns", "Liquidity", "Leverage", "Shareholders", "Momentum", "Risk"];

export function KeyMetricsPanel({ metrics, computedAt }: { metrics: SnapshotMetric[]; computedAt: string | null }) {
  const categories = PANEL_ORDER.filter((c) => metrics.some((m) => m.category === c));
  return (
    <Panel kicker={`LATEST · COMPUTED ${formatDateTime(computedAt)}`} title="Key metrics">
      {metrics.length === 0 ? (
        <p className="panel-copy">No metrics yet. They are computed once SEC financial data has loaded.</p>
      ) : (
        <dl className="metric-list">
          {categories.map((category) => (
            <div className="metric-group" key={category}>
              <h3 className="subheading">{category}</h3>
              {metrics.filter((m) => m.category === category).map((metric) => (
                <div className="metric-row" key={metric.key} title={metricTitle(metric)}>
                  <dt>{metric.label}</dt>
                  <dd>{formatValue(metric.value, metric.unit)}<small>{metric.basis.split("; ").pop()}</small></dd>
                </div>
              ))}
            </div>
          ))}
        </dl>
      )}
      <p className="panel-copy">TTM when the last four quarters are reported, otherwise the latest fiscal year. Hover a row for its formula and dates.</p>
    </Panel>
  );
}
