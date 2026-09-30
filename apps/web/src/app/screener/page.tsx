"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { useScreenMetrics } from "@/components/PeersTab";
import { ErrorNotice, PageHeading, Panel } from "@/components/ui";
import { api, type ScreenMetric, type ScreenRequest, type ScreenResponse } from "@/lib/api/endpoints";
import { formatDate, formatInteger, formatValue } from "@/lib/format";
import { toBound } from "@/lib/screener";

const PAGE_SIZE = 50;

type FilterDraft = { id: number; metric: string; min: string; max: string };

function unitHint(unit: ScreenMetric["unit"]): string {
  return unit === "percent" ? "%" : unit === "currency" ? "USD" : unit === "multiple" ? "×" : "";
}

function MetricSelect({ metrics, value, onChange, label }: {
  metrics: ScreenMetric[];
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  const categories = [...new Set(metrics.map((m) => m.category))];
  return (
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {categories.map((category) => (
        <optgroup key={category} label={category}>
          {metrics.filter((m) => m.category === category).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

export default function ScreenerPage() {
  const catalog = useScreenMetrics();
  const sectors = useQuery({ queryKey: ["screen-sectors"], queryFn: api.screenSectors, staleTime: 10 * 60_000 });
  const [filters, setFilters] = useState<FilterDraft[]>([
    { id: 1, metric: "revenue_growth_yoy", min: "10", max: "" },
    { id: 2, metric: "operating_margin", min: "15", max: "" },
  ]);
  const [sector, setSector] = useState("");
  const [sortBy, setSortBy] = useState("market_cap");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [request, setRequest] = useState<ScreenRequest | null>(null);
  const run = useMutation({ mutationFn: (body: ScreenRequest) => api.screen(body) });
  const metrics = catalog.data ?? [];
  const byKey = new Map(metrics.map((m) => [m.key, m]));

  function submit(event: FormEvent, offset = 0) {
    event.preventDefault();
    const body: ScreenRequest = {
      filters: filters.flatMap((f) => {
        const unit = byKey.get(f.metric)?.unit ?? "ratio";
        const min = toBound(f.min, unit);
        const max = toBound(f.max, unit);
        return min === null && max === null ? [] : [{ metric: f.metric, min, max }];
      }),
      sector: sector || null,
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: PAGE_SIZE,
      offset,
    };
    setRequest(body);
    run.mutate(body);
  }

  function page(offset: number) {
    if (!request) return;
    const body = { ...request, offset };
    setRequest(body);
    run.mutate(body);
  }

  function update(id: number, patch: Partial<FilterDraft>) {
    setFilters((current) => current.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  }

  const result: ScreenResponse | undefined = run.data;
  return (
    <>
      <PageHeading kicker="SCREENER · V1" title="Screener"
        subtitle="Filter companies on the latest SEC fundamentals, valuation multiples, and price momentum." />
      <Panel kicker="CRITERIA" title="Filters">
        {catalog.isError && <ErrorNotice error={catalog.error} title="Metric catalog unavailable" />}
        <form className="screener-form" onSubmit={(event) => submit(event)}>
          {filters.map((filter) => {
            const unit = byKey.get(filter.metric)?.unit ?? "ratio";
            return (
              <div className="screener-filter" key={filter.id}>
                <MetricSelect label="Metric" metrics={metrics} value={filter.metric}
                  onChange={(metric) => update(filter.id, { metric })} />
                <input aria-label="Minimum" inputMode="decimal" placeholder={`Min ${unitHint(unit)}`} value={filter.min}
                  onChange={(event) => update(filter.id, { min: event.target.value })} />
                <input aria-label="Maximum" inputMode="decimal" placeholder={`Max ${unitHint(unit)}`} value={filter.max}
                  onChange={(event) => update(filter.id, { max: event.target.value })} />
                <button aria-label="Remove filter" className="icon-button" type="button"
                  onClick={() => setFilters((current) => current.filter((f) => f.id !== filter.id))}><X size={15} /></button>
              </div>
            );
          })}
          <div className="screener-options">
            <button className="button-link" disabled={filters.length >= 12} type="button"
              onClick={() => setFilters((current) => [...current, {
                id: Math.max(0, ...current.map((f) => f.id)) + 1, metric: "roic", min: "", max: "",
              }])}>
              <Plus size={14} /> Add filter
            </button>
            <select aria-label="Sector" value={sector} onChange={(event) => setSector(event.target.value)}>
              <option value="">All SIC divisions</option>
              {(sectors.data ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <MetricSelect label="Sort by" metrics={metrics} value={sortBy} onChange={setSortBy} />
            <div className="segmented" role="group" aria-label="Sort direction">
              {(["desc", "asc"] as const).map((dir) => (
                <button aria-pressed={dir === sortDir} key={dir} onClick={() => setSortDir(dir)} type="button">
                  {dir === "desc" ? "High → low" : "Low → high"}
                </button>
              ))}
            </div>
            <button className="button-primary" disabled={run.isPending || !catalog.data} type="submit">
              {run.isPending ? "Screening…" : "Run screen"}
            </button>
          </div>
        </form>
        <p className="panel-copy">
          Percent filters are entered as percentages (15 means 15%). Metrics use trailing twelve months when four
          quarters are reported, otherwise the latest fiscal year. Only companies whose SEC financial data has been
          loaded are screened; a company missing a filtered metric is excluded, never assumed.
        </p>
      </Panel>

      {run.isError && <ErrorNotice error={run.error} title="Screen failed" />}
      {result && (
        <Panel kicker={`${formatInteger(result.total)} MATCHES · UNIVERSE ${formatInteger(result.universe)} COMPANIES`} title="Results"
          className={run.isPending ? "is-refetching" : ""}>
          {result.rows.length === 0 ? (
            <p className="panel-copy">No company with loaded fundamentals meets every filter.</p>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>SIC division</th>
                    {result.columns.map((key) => (
                      <th className="num" key={key} title={byKey.get(key)?.description}>{byKey.get(key)?.label ?? key}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row) => (
                    <tr key={row.ticker}>
                      <td>
                        <Link href={`/companies/${encodeURIComponent(row.ticker)}?tab=financials`}>{row.ticker}</Link>
                        <small>{row.name}</small>
                      </td>
                      <td>{row.sector ?? "—"}<small>{row.industry}</small></td>
                      {result.columns.map((key) => (
                        <td className="num" key={key} title={row.metrics_as_of[key] ? `Inputs public by ${formatDate(row.metrics_as_of[key])}` : undefined}>
                          {formatValue(row.metrics[key], byKey.get(key)?.unit ?? "ratio")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {request && result.total > PAGE_SIZE && (
            <div className="pager">
              <button className="button-secondary" disabled={request.offset === 0 || run.isPending} type="button"
                onClick={() => page(Math.max(0, request.offset - PAGE_SIZE))}>Previous</button>
              <span>{request.offset + 1}–{Math.min(request.offset + PAGE_SIZE, result.total)} of {formatInteger(result.total)}</span>
              <button className="button-secondary" disabled={request.offset + PAGE_SIZE >= result.total || run.isPending}
                type="button" onClick={() => page(request.offset + PAGE_SIZE)}>Next</button>
            </div>
          )}
        </Panel>
      )}
    </>
  );
}
