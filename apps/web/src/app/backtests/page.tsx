"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Trash2 } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { LineChart } from "@/components/LineChart";
import { ErrorNotice, PageHeading, Panel, StatusPill } from "@/components/ui";
import { api, type BacktestOptions, type BacktestResult } from "@/lib/api/endpoints";
import { annualTable, defaultForm, FACTOR_KEYS, type FormState, formatStat, STAT_ROWS, type Stats, toRequest } from "@/lib/backtest";
import { formatDate, formatInteger, formatValue } from "@/lib/format";
import { signedPct } from "@/lib/quant";

const ZERO = [{ value: 0, title: "0" }];
const money = (v: number) => `$${v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : Math.round(v).toLocaleString("en-US")}`;
const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
const FACTOR_LABELS: Record<string, string> = {
  value: "Value", quality: "Quality", momentum: "Momentum", low_volatility: "Low volatility", growth: "Growth", size: "Size",
};

export default function BacktestsPage() {
  const options = useQuery({ queryKey: ["backtest-options"], queryFn: api.backtestOptions });
  const runs = useQuery({ queryKey: ["backtests"], queryFn: api.backtests });
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? runs.data?.find((r) => r.status === "done")?.id ?? null;
  return (
    <>
      <PageHeading kicker="BACKTESTING" title="Backtests"
        subtitle="Test factor rules on the point-in-time universe, with estimated trading costs, against SPY and an equal-weight universe." />
      {options.isError && <ErrorNotice error={options.error} title="Backtesting unavailable" />}
      <div className="backtest-layout">
        {options.data && <StrategyForm options={options.data} onDone={setSelected} />}
        <Panel kicker="SAVED · PRIVATE TO YOU" title="Your backtests">
          {runs.data && runs.data.length === 0 && <p className="panel-copy">No backtests yet. Run one with the form.</p>}
          {runs.data && runs.data.length > 0 && (
            <RunList runs={runs.data} current={current} onSelect={setSelected} />
          )}
          {options.data && <ul className="source-notes">{options.data.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
        </Panel>
      </div>
      {current && <BacktestView id={current} />}
    </>
  );
}

function StrategyForm({ options, onDone }: { options: BacktestOptions; onDone: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(() => defaultForm(options));
  const built = useMemo(() => toRequest(form), [form]);
  const run = useMutation({
    mutationFn: api.runBacktest,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["backtests"] });
      onDone(result.id);
    },
  });
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    if (built.request) run.mutate(built.request);
  }

  return (
    <Panel kicker="STRATEGY · LONG ONLY · FACTOR RULES" title="New backtest">
      <form className="backtest-form" onSubmit={submit}>
        <label className="wide">Name<input value={form.name} maxLength={120} onChange={(e) => set("name", e.target.value)} /></label>
        <fieldset className="wide">
          <legend>Rank by (weights of factor scores)</legend>
          {FACTOR_KEYS.map((factor) => (
            <label className="factor-weight" key={factor}>
              <span>{FACTOR_LABELS[factor]}</span>
              <input type="range" min={0} max={1} step={0.1} value={form.weights[factor]} aria-label={`${FACTOR_LABELS[factor]} weight`}
                onChange={(e) => set("weights", { ...form.weights, [factor]: Number(e.target.value) })} />
              <output>{form.weights[factor] === 0 ? "off" : form.weights[factor].toFixed(1)}</output>
            </label>
          ))}
        </fieldset>
        <label>Hold
          <select value={form.selection} onChange={(e) => set("selection", e.target.value as FormState["selection"])}>
            <option value="top_n">Top N companies</option>
            <option value="top_quantile">Top fraction</option>
          </select>
        </label>
        {form.selection === "top_n" ? (
          <label>N<input type="number" min={5} max={100} value={form.topN} onChange={(e) => set("topN", Number(e.target.value))} /></label>
        ) : (
          <label>Fraction<input type="number" min={0.05} max={0.5} step={0.05} value={form.topQuantile} onChange={(e) => set("topQuantile", Number(e.target.value))} /></label>
        )}
        <label>Weighting
          <select value={form.weighting} onChange={(e) => set("weighting", e.target.value as FormState["weighting"])}>
            <option value="equal">Equal</option>
            <option value="score">By rank</option>
            <option value="inverse_volatility">Inverse volatility</option>
          </select>
        </label>
        <label>Max weight per stock<input type="number" min={0.01} max={1} step={0.01} value={form.maxWeight} onChange={(e) => set("maxWeight", Number(e.target.value))} /></label>
        <label>Rebalance
          <select value={form.rebalance} onChange={(e) => set("rebalance", e.target.value as FormState["rebalance"])}>
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
          </select>
        </label>
        <label>Starting capital (USD)<input type="number" min={10000} step={10000} value={form.capital} onChange={(e) => set("capital", Number(e.target.value))} /></label>
        <label>From<input type="date" min={options.first_signal ?? undefined} max={options.last_signal ?? undefined} value={form.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label>To (blank = latest)<input type="date" value={form.end} onChange={(e) => set("end", e.target.value)} /></label>
        <details className="wide cost-settings">
          <summary>Trading cost assumptions</summary>
          {options.costs.map((c) => (
            <label key={c.key} title={c.explanation}>
              {c.label}
              <input type="number" step="any" min={0} value={form.costs[c.key]}
                onChange={(e) => set("costs", { ...form.costs, [c.key]: Number(e.target.value) })} />
              <small>{c.explanation}</small>
            </label>
          ))}
          <p className="panel-copy">
            The spread comes from each stock&apos;s daily highs and lows (Corwin–Schultz). For the largest US stocks it reads about
            20–40 basis points, well above typical quoted spreads, so costs here are conservative; the spread cap limits it.
          </p>
        </details>
        <div className="wide form-actions">
          <button className="button-primary" disabled={!built.request || run.isPending} type="submit">
            <Play size={13} /> {run.isPending ? "Running… about 10 seconds" : "Run backtest"}
          </button>
          {built.error && <small className="form-error">{built.error}</small>}
        </div>
      </form>
      {run.isError && <ErrorNotice error={run.error} title="Backtest failed" />}
    </Panel>
  );
}

function RunList({ runs, current, onSelect }: {
  runs: { id: string; name: string; status: string; created_at: string; summary: Record<string, unknown> }[];
  current: string | null;
  onSelect: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: api.deleteBacktest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backtests"] }),
  });
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead><tr><th>Backtest</th><th className="num">CAGR</th><th className="num">SPY</th><th className="num">Sharpe</th><th className="num">Largest fall</th><th /></tr></thead>
        <tbody>
          {runs.map((r) => {
            const s = r.summary as Record<string, number | null>;
            return (
              <tr className={r.id === current ? "row-subject" : undefined} key={r.id}>
                <td>
                  <button className="button-link" type="button" onClick={() => onSelect(r.id)}>{r.name}</button>
                  <small>{r.status === "done" ? `${formatDate(String(s.start))} – ${formatDate(String(s.end))}` : "failed"}</small>
                </td>
                <td className="num">{formatValue(s.cagr, "percent")}</td>
                <td className="num">{formatValue(s.spy_cagr, "percent")}</td>
                <td className="num">{s.sharpe?.toFixed(2) ?? "—"}</td>
                <td className="num">{formatValue(s.max_drawdown, "percent")}</td>
                <td><button aria-label={`Delete ${r.name}`} className="button-secondary" type="button" onClick={() => remove.mutate(r.id)}><Trash2 size={13} /></button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type Results = {
  stats: Record<"strategy" | "equal_weight" | "spy" | "before_costs", Stats>;
  series: Record<string, [string, number][]>;
  periods: Record<"strategy" | "spy" | "equal_weight", { years: Record<string, number>; months: Record<string, number> }>;
  hit_rate_vs_spy: number | null;
  trading: Record<string, number | null> & { costs: Record<string, number> };
  holdings: { as_of: string | null; signal_date: string | null; positions: { ticker: string; name: string; weight: number }[] };
  coverage: { as_of: string; universe: number; eligible: number; selected: number }[];
  risk_free: string;
  warnings: string[];
  assumptions: string[];
};

function BacktestView({ id }: { id: string }) {
  const query = useQuery({ queryKey: ["backtest", id], queryFn: () => api.backtest(id) });
  if (query.isPending) return <div className="chart-placeholder">Loading backtest…</div>;
  if (query.isError) return <ErrorNotice error={query.error} title="Backtest unavailable" />;
  return <Result data={query.data} />;
}

function Result({ data }: { data: BacktestResult }) {
  const r = data.results as unknown as Results;
  const charts = useMemo(() => {
    if (data.status !== "done") return null;
    const line = (key: string) => (r.series[key] ?? []).map(([date, value]) => ({ date, value }));
    return {
      growth: [
        { name: "Strategy (after costs)", points: line("strategy") },
        { name: "Strategy before costs", points: line("before_costs"), width: 1 as const },
        { name: "Equal-weight universe", points: line("equal_weight"), width: 1 as const },
        { name: "SPY (total return)", points: line("spy") },
      ],
      drawdown: [
        { name: "Strategy", points: line("drawdown") },
        { name: "SPY", points: line("spy_drawdown") },
      ],
      excess: [{ name: "Strategy minus SPY, trailing 12 months", points: line("rolling_excess_12m") }],
      coverage: [{
        name: "Share of the universe that could be held",
        points: r.coverage.map((c) => ({ date: c.as_of, value: c.universe ? c.eligible / c.universe : null })),
      }],
    };
  }, [data, r]);
  if (data.status !== "done" || !charts) {
    return <Panel kicker="BACKTEST" title={data.name}><ErrorNotice error={new Error(data.error ?? "Failed.")} title="This backtest did not run" /></Panel>;
  }
  const s = r.stats;
  const spec = data.spec as { factors: { factor: string; weight: number }[]; top_n: number; selection: string; top_quantile: number; weighting: string; rebalance: string; capital: number };
  const summary = data.summary as Record<string, number | null>;
  const dsr = summary.dsr;
  return (
    <div className="company-tab-body">
      <Panel kicker={`${formatDate(String(s.strategy.start))} – ${formatDate(String(s.strategy.end))} · ENGINE ${data.code_version} · ${data.duration_ms / 1000}s`}
        title={data.name}
        action={<StatusPill tone="neutral">{spec.factors.map((f) => `${FACTOR_LABELS[f.factor]} ${f.weight}`).join(" + ")}</StatusPill>}>
        <div className="scenario-cards">
          <section className="scenario-card scenario-base"><span className="section-kicker">STRATEGY, AFTER COSTS</span><strong>{formatValue(s.strategy.cagr as number, "percent")}</strong><small>a year · Sharpe {formatStat(s.strategy.sharpe, "ratio")} · largest fall {formatValue(s.strategy.max_drawdown as number, "percent")}</small></section>
          <section className="scenario-card"><span className="section-kicker">BEFORE COSTS</span><strong>{formatValue(s.before_costs.cagr as number, "percent")}</strong><small>costs took {formatValue(((s.before_costs.cagr as number) - (s.strategy.cagr as number)), "percent")} a year</small></section>
          <section className="scenario-card"><span className="section-kicker">SPY</span><strong>{formatValue(s.spy.cagr as number, "percent")}</strong><small>Sharpe {formatStat(s.spy.sharpe, "ratio")} · largest fall {formatValue(s.spy.max_drawdown as number, "percent")}</small></section>
          <section className="scenario-card"><span className="section-kicker">EQUAL-WEIGHT UNIVERSE</span><strong>{formatValue(s.equal_weight.cagr as number, "percent")}</strong><small>Sharpe {formatStat(s.equal_weight.sharpe, "ratio")} · same rebalancing and costs</small></section>
        </div>
        <LineChart label="Growth of the strategy, before costs, equal-weight universe and SPY, log scale" series={charts.growth} height={320} logScale format={money} />
        <p className="panel-copy">
          Holding the top {spec.selection === "top_n" ? spec.top_n : `${spec.top_quantile * 100}%`} by the blended score, weighted {spec.weighting.replace("_", " ")}, rebalanced {spec.rebalance}, from {money(spec.capital)}.
          {" "}Beat SPY in {r.hit_rate_vs_spy === null ? "—" : pct(r.hit_rate_vs_spy)} of months. Risk-free rate: {r.risk_free}.
        </p>
      </Panel>
      <div className="quant-grid">
        <Panel kicker="RISK" title="Falls from the previous high">
          <LineChart label="Drawdowns of the strategy and SPY" series={charts.drawdown} height={220} format={pct} guides={ZERO} />
        </Panel>
        <Panel kicker="RELATIVE" title="Trailing 12-month return versus SPY">
          <LineChart label="Strategy minus SPY over trailing 12 months" series={charts.excess} height={220} format={pct} guides={ZERO} />
        </Panel>
      </div>
      <div className="quant-grid">
        <Panel kicker="STATISTICS" title="Strategy and comparisons">
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr><th>Measure</th><th className="num">Strategy</th><th className="num">Before costs</th><th className="num">Equal weight</th><th className="num">SPY</th></tr></thead>
              <tbody>
                {STAT_ROWS.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    {(["strategy", "before_costs", "equal_weight", "spy"] as const).map((k) => <td className="num" key={k}>{formatStat(s[k][row.key], row.unit)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="panel-copy">
            {dsr === null || dsr === undefined
              ? "This is your first backtest over this period, so there is nothing to deflate yet."
              : `Deflated: ${pct(dsr)} probability that it truly beats SPY after allowing for your ${summary.trials} backtests over overlapping periods — the best of many tries looks good by chance.`}
            {" "}Probabilities test the return above SPY (probabilistic and deflated Sharpe ratios, Bailey and López de Prado).
          </p>
        </Panel>
        <Panel kicker="CALENDAR YEARS" title="Annual returns">
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr><th>Year</th><th className="num">Strategy</th><th className="num">SPY</th><th className="num">Difference</th></tr></thead>
              <tbody>
                {annualTable(r.periods.strategy.years, r.periods.spy.years).map(([year, own, bench, diff]) => (
                  <tr key={year}><td>{year}</td><td className="num">{signedPct(own)}</td><td className="num">{signedPct(bench)}</td><td className={`num ${diff !== null && diff < 0 ? "neg" : ""}`}>{signedPct(diff)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="panel-copy">First and last years are partial.</p>
        </Panel>
      </div>
      <div className="quant-grid">
        <Panel kicker="TRADING" title="Turnover and costs">
          <dl className="stat-grid compact">
            <div><dt>Rebalances</dt><dd>{formatInteger(r.trading.rebalances)}</dd></div>
            <div><dt>Orders</dt><dd>{formatInteger(r.trading.trades)}<small>{r.trading.capped_orders} cut by the volume cap</small></dd></div>
            <div><dt>Turnover a year</dt><dd>{formatValue(r.trading.annual_turnover, "percent")}</dd></div>
            <div><dt>Costs a year</dt><dd>{r.trading.costs_bps_per_year?.toFixed(0) ?? "—"} bps<small>of average value</small></dd></div>
            <div><dt>Spread</dt><dd>{money(r.trading.costs.spread)}</dd></div>
            <div><dt>Market impact</dt><dd>{money(r.trading.costs.impact)}</dd></div>
            <div><dt>Commission</dt><dd>{money(r.trading.costs.commission)}</dd></div>
            <div><dt>Average holdings</dt><dd>{r.trading.average_positions?.toFixed(1)}</dd></div>
          </dl>
          <h3 className="subheading">Universe coverage</h3>
          <LineChart label="Share of universe members that could be held on each signal date" series={charts.coverage} height={150} format={pct} />
          <p className="panel-copy">Members without price history (mostly companies delisted since) could not be held. Early years cover less of the universe, which flatters results toward survivors.</p>
        </Panel>
        <Panel kicker={`HOLDINGS · SIGNAL ${formatDate(r.holdings.signal_date)}`} title="Latest portfolio">
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr><th>Company</th><th className="num">Weight</th></tr></thead>
              <tbody>
                {r.holdings.positions.map((p) => (
                  <tr key={p.ticker}><td><b>{p.ticker}</b><small>{p.name}</small></td><td className="num">{formatValue(p.weight, "percent")}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
      <Panel kicker="METHOD" title="Assumptions">
        <ul className="source-notes">{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        {r.warnings.length > 0 && <ul className="valuation-warnings">{r.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        <p className="panel-copy">A backtest shows what a rule would have done; it does not predict what it will do.</p>
      </Panel>
    </div>
  );
}
