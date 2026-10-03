"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { LineChart } from "@/components/LineChart";
import { XYChart } from "@/components/MiniCharts";
import { ErrorNotice, PageHeading, Panel, StatusPill } from "@/components/ui";
import { api, type ModelDetail, type ModelSummary, type ResearchStatus } from "@/lib/api/endpoints";
import { formatDate, formatInteger, formatValue } from "@/lib/format";
import { featureLabel, signedPct } from "@/lib/quant";

const num = (v: unknown, digits = 3) => (typeof v === "number" ? v.toFixed(digits) : "—");
const DIAGONAL: [number, number][] = [[0, 0], [1, 1]];
const ZERO = [{ value: 0, title: "0" }];

export default function ModelsPage() {
  const status = useQuery({ queryKey: ["research-status"], queryFn: api.researchStatus });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const [selected, setSelected] = useState<string | null>(null);
  const active = (models.data ?? []).filter((m) => m.status === "active");
  const current = selected ?? active[0]?.id ?? null;
  return (
    <>
      <PageHeading kicker="QUANTITATIVE RESEARCH" title="Models"
        subtitle="Return models trained point in time, evaluated walk-forward against a simple baseline, with calibration, SHAP, and a journal of every prediction." />
      {status.isError && <ErrorNotice error={status.error} title="Research status unavailable" />}
      {status.data && <StatusPanel data={status.data} />}
      {models.isError && <ErrorNotice error={models.error} title="Models unavailable" />}
      {models.data && models.data.length === 0 && (
        <Panel kicker="MODELS" title="No models trained yet">
          <p className="panel-copy">
            Training needs the universe, several years of prices, and month-end feature snapshots. The worker trains
            monthly once there are enough labelled rows.
          </p>
        </Panel>
      )}
      {active.length > 0 && (
        <div className="model-cards">
          {active.map((m) => <ModelCard key={m.id} model={m} selected={m.id === current} onSelect={() => setSelected(m.id)} />)}
        </div>
      )}
      {current && <ModelDetailView id={current} />}
    </>
  );
}

function StatusPanel({ data }: { data: ResearchStatus }) {
  const gaps = data.survivorship.map((r) => ({ date: String(r.as_of), value: Number(r.missing_share) }));
  return (
    <Panel kicker="RESEARCH DATA" title="Universe, prices, and sources">
      <dl className="stat-grid">
        <div><dt>Universe month ends</dt><dd>{formatInteger(data.universe_dates)}<small>latest {formatDate(data.latest_universe_date)}</small></dd></div>
        <div><dt>Members with prices (latest)</dt><dd>{data.members_with_prices_latest} of {data.members_latest}</dd></div>
        <div><dt>Price histories loaded</dt><dd>{data.prices_loaded} of {data.price_targets}<small>{data.prices_not_found} not found at Tiingo</small></dd></div>
        <div><dt>Feature snapshots</dt><dd>{formatInteger(data.feature_dates)} month ends<small>latest {formatDate(data.latest_feature_date)}</small></dd></div>
        <div><dt>Macro (FRED)</dt><dd>{data.fred_configured ? `${data.macro_series_loaded} series` : "key not set"}</dd></div>
        <div><dt>Bitcoin days</dt><dd>{formatInteger(data.bitcoin_days)}</dd></div>
        <div><dt>13F quarters</dt><dd>{data.ownership_periods.map((d) => formatDate(d)).join(", ") || "none"}</dd></div>
        <div><dt>Model versions</dt><dd>{data.models}</dd></div>
      </dl>
      {gaps.length > 0 && (
        <>
          <h3 className="subheading">Survivorship gap</h3>
          <p className="panel-copy">
            Share of each month&apos;s 200 largest companies (by revenue known then) with no usable price history, mostly
            delisted or acquired companies. Models train only on companies with prices, so results are biased toward survivors
            by this much.
          </p>
          <LineChart label="Share of universe members without price history by month" height={160}
            series={[{ name: "Missing price history", points: gaps }]} format={(v) => `${(v * 100).toFixed(0)}%`} />
        </>
      )}
    </Panel>
  );
}

function ModelCard({ model, selected, onSelect }: { model: ModelSummary; selected: boolean; onSelect: () => void }) {
  const o = model.oos as Record<string, number | null | number[] | boolean>;
  const live = model.live as Record<string, number | boolean | null>;
  return (
    <button className={`model-card${selected ? " selected" : ""}`} type="button" onClick={onSelect} aria-pressed={selected}>
      <span className="section-kicker">{model.horizon_days} TRADING DAYS · TRAINED THROUGH {formatDate(model.trained_through).toUpperCase()}</span>
      <b>{model.target}</b>
      <span>
        <StatusPill tone={o.validated ? "ok" : "warn"}>{o.validated ? "VALIDATED" : "NOT VALIDATED"}</StatusPill>
      </span>
      <dl>
        <div><dt>AUC (baseline)</dt><dd>{num(o.auc)} <small>({num(o.baseline_auc)})</small></dd></div>
        <div><dt>Brier (base rate)</dt><dd>{num(o.brier, 4)} <small>({num(o.brier_base_rate, 4)})</small></dd></div>
        <div><dt>Rank IC, t-stat</dt><dd>{num(o.rank_ic_mean)} <small>t {num(o.rank_ic_t, 1)}</small></dd></div>
        <div><dt>Top − bottom decile</dt><dd>{signedPct(o.decile_spread as number | null, 2)}</dd></div>
        <div><dt>Live predictions scored</dt><dd>{String(live.scored ?? 0)}</dd></div>
      </dl>
      {!o.validated && <small>Fails the rule: {String((model.oos as Record<string, unknown>).validation_rule)}.</small>}
      <small>Out of sample, {Array.isArray(o.test_years) ? `${o.test_years[0]}–${o.test_years.at(-1)}` : ""}, {formatInteger(o.rows as number)} predictions</small>
    </button>
  );
}

type Metrics = Record<string, unknown> & { rank_ic?: { series?: [string, number][] } };

function ModelDetailView({ id }: { id: string }) {
  const query = useQuery({ queryKey: ["model", id], queryFn: () => api.model(id) });
  if (query.isPending) return <div className="chart-placeholder">Loading model…</div>;
  if (query.isError) return <ErrorNotice error={query.error} title="Model unavailable" />;
  const m: ModelDetail = query.data;
  const summary = (m.evaluation.summary ?? {}) as Record<string, unknown>;
  const model = (summary.model ?? {}) as Metrics;
  const baseline = (summary.baseline ?? {}) as Metrics;
  const curve = (summary.calibration_curve ?? []) as { predicted: number; observed: number; count: number }[];
  const folds = (m.evaluation.folds ?? []) as { year: number; test_rows: number; model: Metrics; baseline: Metrics; calibrator: { chosen?: string } }[];
  const shap = ((m.importance.shap ?? []) as { feature: string; mean_abs_shap: number }[]).slice(0, 15);
  const maxShap = shap[0]?.mean_abs_shap || 1;
  const ic = (model.rank_ic?.series ?? []).map(([date, value]) => ({ date, value }));
  const segments = (key: string) => Object.entries((summary[key] ?? {}) as Record<string, { rows: number; auc: number | null; brier: number; rank_ic_mean: number | null }>);
  return (
    <div className="company-tab-body">
      <Panel kicker={`MODEL ${m.name} · ${m.code_version} · FEATURES ${m.feature_set_version}`} title="Out-of-sample evaluation"
        action={<StatusPill tone={m.status === "active" ? "ok" : "off"}>{m.status.toUpperCase()}</StatusPill>}>
        <p className="panel-copy">
          Walk-forward: each year is predicted by a model trained only on labels known at least {String(m.params.embargo_days)} days before
          the year began, calibrated on the last {String(m.params.calibration_months)} months of its training window. The baseline is a
          regularised logistic regression on the same inputs. An AUC of 0.5 and a rank IC of 0 mean no skill.
        </p>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Metric</th><th className="num">Model</th><th className="num">Baseline</th></tr></thead>
            <tbody>
              <tr><td>AUC</td><td className="num">{num(model.auc)}</td><td className="num">{num(baseline.auc)}</td></tr>
              <tr><td>Brier score (base rate {num(model.brier_base_rate, 4)})</td><td className="num">{num(model.brier, 4)}</td><td className="num">{num(baseline.brier, 4)}</td></tr>
              <tr><td>Log loss</td><td className="num">{num(model.log_loss, 4)}</td><td className="num">{num(baseline.log_loss, 4)}</td></tr>
              <tr><td>Hit rate</td><td className="num">{formatValue(model.hit_rate as number, "percent")}</td><td className="num">{formatValue(baseline.hit_rate as number, "percent")}</td></tr>
              <tr><td>Rank IC (mean per month)</td><td className="num">{num((model.rank_ic as Record<string, number>)?.mean)}</td><td className="num">{num((baseline.rank_ic as Record<string, number>)?.mean)}</td></tr>
              <tr><td>Top − bottom decile excess return</td><td className="num">{signedPct(model.decile_spread as number, 2)}</td><td className="num">{signedPct(baseline.decile_spread as number, 2)}</td></tr>
              <tr><td>10–90% range coverage (target 80%)</td><td className="num">{formatValue((summary.quantiles as Record<string, number>)?.coverage_10_90, "percent")}</td><td className="num">—</td></tr>
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="quant-grid">
        <Panel kicker={`CALIBRATION · ${String(m.calibration.method ?? "none").toUpperCase()}`} title="Predicted vs observed">
          <XYChart label="Calibration curve: predicted probability against observed frequency" height={260}
            xLabel="Predicted probability" yFormat={(v) => `${(v * 100).toFixed(0)}%`}
            series={[
              { name: "Model", points: curve.map((b) => [b.predicted, b.observed] as [number, number]), markers: true },
              { name: "Perfect calibration", points: DIAGONAL, dashed: true },
            ]} />
          <p className="panel-copy">Each point is a probability bin; points on the dashed line mean the stated probabilities came true as often as stated.</p>
        </Panel>
        <Panel kicker="RANK INFORMATION COEFFICIENT" title="Monthly rank IC">
          <LineChart label="Spearman correlation of predictions with realised excess returns, by month" height={260}
            series={[{ name: "Rank IC", points: ic }]} guides={ZERO} format={(v) => v.toFixed(2)} />
          <p className="panel-copy">Correlation between the model&apos;s ranking and realised excess returns across companies, each month.</p>
        </Panel>
      </div>
      <div className="quant-grid">
        <Panel kicker="SHAP · LAST 12 MONTHS" title="What the model relies on">
          <ul className="importance-list">
            {shap.map((f) => (
              <li key={f.feature}>
                <span>{featureLabel(f.feature)}</span>
                <span className="importance-bar"><i style={{ width: `${(f.mean_abs_shap / maxShap) * 100}%` }} /></span>
                <span className="num">{f.mean_abs_shap.toFixed(4)}</span>
              </li>
            ))}
          </ul>
          <p className="panel-copy">Mean absolute SHAP contribution in log-odds. Importance is not causation.</p>
        </Panel>
        <Panel kicker="SEGMENTS" title="By market regime and sector">
          {(["by_regime", "by_sector"] as const).map((key) => (
            <div className="table-wrap" key={key}>
              <table className="data-table">
                <thead><tr><th>{key === "by_regime" ? "Regime" : "Sector"}</th><th className="num">Rows</th><th className="num">AUC</th><th className="num">Rank IC</th></tr></thead>
                <tbody>
                  {segments(key).map(([name, s]) => (
                    <tr key={name}><td>{name}</td><td className="num">{formatInteger(s.rows)}</td><td className="num">{num(s.auc)}</td><td className="num">{num(s.rank_ic_mean)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </Panel>
      </div>
      <Panel kicker="WALK-FORWARD FOLDS" title="Year by year">
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Test year</th><th className="num">Predictions</th><th className="num">AUC</th><th className="num">Baseline AUC</th><th className="num">Rank IC</th><th className="num">Brier</th><th>Calibrator</th></tr></thead>
            <tbody>
              {folds.map((f) => (
                <tr key={f.year}>
                  <td>{f.year}</td>
                  <td className="num">{formatInteger(f.test_rows)}</td>
                  <td className="num">{num(f.model.auc)}</td>
                  <td className="num">{num(f.baseline.auc)}</td>
                  <td className="num">{num((f.model.rank_ic as Record<string, number>)?.mean)}</td>
                  <td className="num">{num(f.model.brier, 4)}</td>
                  <td>{f.calibrator.chosen ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="panel-copy">Trained {formatDate(m.trained_from)} – {formatDate(m.trained_through)} on {m.feature_names.length} inputs. Parameters: learning rate {String(m.params.learning_rate)}, {String(m.params.rounds)} rounds, {String(m.params.num_leaves)} leaves, at least {String(m.params.min_data_in_leaf)} rows per leaf.</p>
      </Panel>
    </div>
  );
}
