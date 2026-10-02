"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save, Trash2, TriangleAlert } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { api, type RelativeValuation, type ValuationGrid, type ValuationOut, type ValuationScenario } from "@/lib/api/endpoints";
import { formatDate, formatDateTime, formatPercent, formatValue } from "@/lib/format";
import { useDebounced } from "@/lib/use-debounced";
import {
  buildRequest, CAPM_FIELDS, DRIVER_LABELS, type FieldSpec, formFrom, type FormState, gridRange, heatTone,
  MODEL_FIELDS, MODEL_LABELS, OVERRIDE_FIELD, scenarioFields, unitSuffix, type ValuationDefaults,
  type ValuationModel, type ValuationRequest,
} from "@/lib/valuation";
import { ErrorNotice, Panel, StatusPill } from "./ui";

const pct = (value: number | null | undefined) => formatValue(value, "percent");
const money = (value: number | null | undefined) => formatValue(value, "currency");
const perShare = (value: number | null | undefined) => formatValue(value, "per_share");
const upsideText = (value: number | null | undefined) =>
  value === null || value === undefined ? null : `${formatPercent(value * 100, 1)} vs price`;

export function ValuationTab({ ticker }: { ticker: string }) {
  const defaults = useQuery({
    queryKey: ["valuation-defaults", ticker],
    queryFn: () => api.valuationDefaults(ticker),
    staleTime: 5 * 60_000,
  });
  if (defaults.isPending) {
    return <div className="company-tab-body"><div className="chart-placeholder">Building default assumptions from SEC filings…</div></div>;
  }
  if (defaults.isError) {
    return <div className="company-tab-body"><ErrorNotice error={defaults.error} title="Valuation unavailable" /></div>;
  }
  return <ValuationWorkspace key={ticker} ticker={ticker} defaults={defaults.data} />;
}

function ValuationWorkspace({ ticker, defaults }: { ticker: string; defaults: ValuationDefaults }) {
  const initial = useMemo(() => formFrom(defaults), [defaults]);
  const [model, setModel] = useState<ValuationModel>(defaults.recommended);
  const [form, setForm] = useState<FormState>(initial);
  const [loadedRun, setLoadedRun] = useState<string | null>(null);
  const built = useMemo(
    () => buildRequest(model, form, defaults.sources, initial),
    [model, form, defaults.sources, initial],
  );
  const request = useDebounced(built.request, 300);
  const available = defaults.available.includes(model);
  const result = useQuery({
    queryKey: ["valuation", ticker, request],
    queryFn: ({ signal }) => api.computeValuation(ticker, request!, signal),
    enabled: request !== null && available,
    placeholderData: keepPreviousData,
    retry: false,
  });

  if (defaults.available.length === 0) {
    return (
      <div className="company-tab-body">
        <Panel kicker="VALUATION" title="No model can be built yet">
          <ul className="valuation-warnings">
            {groupReasons(defaults.unavailable).map(([models, reason]) => (
              <li key={reason}><b>{models}:</b> {reason}</li>
            ))}
            {defaults.warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </Panel>
      </div>
    );
  }

  const set = (key: string, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    setLoadedRun(null);
  };
  const data = result.data;
  const base = data?.scenarios.find((s) => s.name === "base");

  return (
    <div className="company-tab-body">
      <Panel
        kicker={`VALUATION · ${defaults.basis ?? "LATEST"} · FORMULAS ${defaults.formula_version}`}
        title="Intrinsic value"
        action={<ModelPicker defaults={defaults} model={model} onChange={(next) => { setModel(next); setLoadedRun(null); }} />}
      >
        {defaults.warnings.length > 0 && (
          <ul className="valuation-warnings">
            {defaults.warnings.map((warning) => <li key={warning}><TriangleAlert size={13} /> {warning}</li>)}
          </ul>
        )}
        {loadedRun && <p className="panel-copy">Showing saved run “{loadedRun}”. Editing any input starts a new, unsaved valuation.</p>}
        {result.isError && <ErrorNotice error={result.error} title="These assumptions cannot be valued" />}
        {!request && Object.keys(built.errors).length > 0 && (
          <p className="panel-copy">Fix the highlighted inputs to update the valuation.</p>
        )}
        {data && (
          <div className={result.isFetching ? "is-refetching" : undefined}>
            <ScenarioCards scenarios={data.scenarios} price={data.price} priceDate={data.price_date} />
            <div className="valuation-summary">
              {base?.result && request && <ValueBridge model={data.model} request={request} scenario={base} />}
              <RatesSummary data={data} />
            </div>
          </div>
        )}
      </Panel>

      <div className="valuation-layout">
        <AssumptionsPanel
          model={model} form={form} initial={initial} errors={built.errors} sources={defaults.sources}
          onChange={set} onReset={() => { setForm(initial); setLoadedRun(null); }}
        />
        <div className="valuation-results">
          {base?.result && data && <ProjectionPanel model={data.model} scenario={base} />}
          {data && data.sensitivity.map((grid) => (
            <SensitivityPanel grid={grid} key={`${grid.row_label}-${grid.column_label}`} price={data.price} />
          ))}
        </div>
      </div>

      <div className="valuation-layout valuation-layout-even">
        <SavedRuns
          ticker={ticker} request={built.request}
          onLoad={(run) => {
            setModel(run.model);
            setForm(formFrom(run.request as Partial<ValuationRequest>));
            setLoadedRun(run.name);
          }}
        />
        <RelativePanel ticker={ticker} />
      </div>
    </div>
  );
}

/** "Discounted cash flow, Residual income" → one shared reason, listed once. */
function groupReasons(unavailable: Record<string, string>): [string, string][] {
  const byReason = new Map<string, string[]>();
  for (const [key, reason] of Object.entries(unavailable)) {
    byReason.set(reason, [...(byReason.get(reason) ?? []), MODEL_LABELS[key as ValuationModel] ?? key]);
  }
  return [...byReason].map(([reason, models]) => [models.join(", "), reason]);
}

function ModelPicker({ defaults, model, onChange }: {
  defaults: ValuationDefaults; model: ValuationModel; onChange: (model: ValuationModel) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label="Valuation model">
      {(["dcf", "rim", "ddm"] as const).map((key) => {
        const enabled = defaults.available.includes(key);
        return (
          <button aria-pressed={model === key} disabled={!enabled} key={key} type="button" onClick={() => onChange(key)}
            title={enabled ? MODEL_LABELS[key] : defaults.unavailable[key]}>
            {key.toUpperCase()}{defaults.recommended === key ? " ★" : ""}
          </button>
        );
      })}
    </div>
  );
}

const SCENARIO_LABELS = { bear: "Bear", base: "Base", bull: "Bull" } as const;

function ScenarioCards({ scenarios, price, priceDate }: {
  scenarios: ValuationScenario[]; price: number | null; priceDate: string | null;
}) {
  return (
    <div className="scenario-cards">
      {scenarios.map((scenario) => {
        const value = scenario.result?.per_share;
        const direction = (scenario.upside ?? 0) >= 0 ? "up" : "down";
        return (
          <section className={`scenario-card scenario-${scenario.name}`} key={scenario.name}>
            <span className="section-kicker">{SCENARIO_LABELS[scenario.name].toUpperCase()} CASE</span>
            {scenario.result ? (
              <>
                <strong>{perShare(value)}</strong>
                <small className={scenario.upside === null ? undefined : `price-change price-${direction}`}>
                  {upsideText(scenario.upside) ?? "No stored price to compare"}
                </small>
                <small>Discount {pct(scenario.result.discount_rate)} · terminal growth {pct(scenario.result.terminal_growth)}</small>
              </>
            ) : (
              <p className="panel-copy">{scenario.error}</p>
            )}
          </section>
        );
      })}
      <section className="scenario-card scenario-price">
        <span className="section-kicker">LAST CLOSE</span>
        <strong>{perShare(price)}</strong>
        <small>{priceDate ? formatDate(priceDate) : "No stored price (needs a market data provider)"}</small>
      </section>
    </div>
  );
}

function ValueBridge({ model, request, scenario }: {
  model: ValuationModel; request: ValuationRequest; scenario: ValuationScenario;
}) {
  const r = scenario.result!;
  const rows: [string, string, boolean?][] =
    model === "dcf" && request.dcf ? [
      ["PV of cash flows, years 1–10", money(r.pv_explicit)],
      [`PV of terminal value (${pct(r.terminal_share)} of total)`, money(r.pv_terminal)],
      ["Enterprise value", money(r.enterprise_value), true],
      ["− Debt", money(request.dcf.debt)],
      ["+ Cash and investments", money(request.dcf.cash)],
      ["Equity value", money(r.equity_value), true],
      ["÷ Shares", formatValue(request.dcf.shares, "shares")],
      ["Value per share", perShare(r.per_share), true],
    ] : model === "rim" && request.rim ? [
      ["Book equity today", money(request.rim.book_value)],
      ["+ PV of residual income, years 1–10", money(r.pv_explicit)],
      [`+ PV of terminal value (${pct(r.terminal_share)} of total)`, money(r.pv_terminal)],
      ["Equity value", money(r.equity_value), true],
      ["÷ Shares", formatValue(request.rim.shares, "shares")],
      ["Value per share", perShare(r.per_share), true],
    ] : [
      ["PV of dividends, years 1–10", perShare(r.pv_explicit)],
      [`PV of terminal value (${pct(r.terminal_share)} of total)`, perShare(r.pv_terminal)],
      ["Value per share", perShare(r.per_share), true],
    ];
  return (
    <dl className="value-bridge" aria-label="Base case value bridge">
      {rows.map(([label, value, total]) => (
        <div className={total ? "total" : undefined} key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function RatesSummary({ data }: { data: ValuationOut }) {
  const rates = data.rates;
  return (
    <dl className="value-bridge rates">
      <div><dt>Cost of equity (CAPM)</dt><dd>{pct(rates.cost_of_equity)}</dd></div>
      {rates.after_tax_cost_of_debt !== null && <div><dt>After-tax cost of debt</dt><dd>{pct(rates.after_tax_cost_of_debt)}</dd></div>}
      {rates.equity_weight !== null && <div><dt>Equity weight</dt><dd>{pct(rates.equity_weight)}</dd></div>}
      <div className="total">
        <dt>{data.model === "dcf" ? "WACC" : "Cost of equity"}{rates.discount_rate_source === "override" ? " (override)" : ""}</dt>
        <dd>{pct(rates.discount_rate)}</dd>
      </div>
    </dl>
  );
}

function Field({ field, value, initial, error, source, onChange }: {
  field: FieldSpec; value: string; initial: string; error?: string; source?: string; onChange: (value: string) => void;
}) {
  const edited = value.trim() !== initial.trim();
  const id = `field-${field.key.replaceAll(".", "-")}`;
  return (
    <label className={`valuation-field${error ? " invalid" : ""}${edited ? " edited" : ""}`} htmlFor={id}>
      <span>{field.label}</span>
      <span className="field-input">
        <input id={id} inputMode="decimal" placeholder={field.optional ? `blank = ${field.optional}` : undefined}
          value={value} onChange={(event) => onChange(event.target.value)} aria-invalid={Boolean(error)} />
        <i>{unitSuffix(field.unit).trim()}</i>
      </span>
      <small>
        {error ?? (edited
          ? `Edited · default ${initial || "blank"}${initial ? unitSuffix(field.unit) : ""}`
          : source ?? (field.optional ? `Blank: ${field.optional}` : ""))}
      </small>
    </label>
  );
}

function AssumptionsPanel({ model, form, initial, errors, sources, onChange, onReset }: {
  model: ValuationModel; form: FormState; initial: FormState; errors: Record<string, string>;
  sources: Record<string, string>; onChange: (key: string, value: string) => void; onReset: () => void;
}) {
  const fieldProps = (field: FieldSpec) => ({
    field, value: form[field.key] ?? "", initial: initial[field.key] ?? "", error: errors[field.key],
    source: field.source ? sources[field.source] : undefined, onChange: (value: string) => onChange(field.key, value),
  });
  return (
    <Panel kicker="ASSUMPTIONS" title={MODEL_LABELS[model]}
      action={<button className="button-secondary" type="button" onClick={onReset}><RotateCcw size={13} /> Reset</button>}>
      <form className="valuation-form" onSubmit={(event) => event.preventDefault()}>
        <fieldset>
          <legend>Operating drivers</legend>
          {MODEL_FIELDS[model].map((field) => <Field key={field.key} {...fieldProps(field)} />)}
          {model === "dcf" && (
            <label className="check">
              <input checked={form["dcf.mid_year"] === "true"} type="checkbox"
                onChange={(event) => onChange("dcf.mid_year", event.target.checked ? "true" : "false")} />
              Mid-year discounting
            </label>
          )}
        </fieldset>
        <fieldset>
          <legend>Cost of capital (CAPM)</legend>
          {CAPM_FIELDS.filter((f) => model === "dcf" || !["capm.pre_tax_cost_of_debt", "capm.equity_value", "capm.debt_value"].includes(f.key))
            .map((field) => <Field key={field.key} {...fieldProps(field)} />)}
          <Field {...fieldProps(OVERRIDE_FIELD)} />
        </fieldset>
        {(["bear", "bull"] as const).map((name) => (
          <fieldset className="scenario-shifts" key={name}>
            <legend>{name === "bear" ? "Bear" : "Bull"} case shifts (percentage points)</legend>
            {scenarioFields(name).map((field) => <Field key={field.key} {...fieldProps(field)} />)}
          </fieldset>
        ))}
      </form>
      <p className="panel-copy">
        Amounts are in millions. Each default shows where it came from; edited values are recorded as edited when a run is saved.
        {model === "dcf" && " Years 1–5 use the stated growth and margin; years 6–10 fade linearly to terminal growth and the long-run margin."}
      </p>
    </Panel>
  );
}

function ProjectionPanel({ model, scenario }: { model: ValuationModel; scenario: ValuationScenario }) {
  const years = scenario.result!.years;
  const columns: [string, (y: (typeof years)[number]) => string][] =
    model === "dcf" ? [
      ["Revenue", (y) => money(y.revenue)], ["Growth", (y) => pct(y.growth)], ["Margin", (y) => pct(y.margin)],
      ["Operating income", (y) => money(y.operating_income)], ["NOPAT", (y) => money(y.nopat)],
      ["Reinvestment", (y) => money(y.reinvestment)], ["Free cash flow", (y) => money(y.cash_flow)],
      ["Discount factor", (y) => y.discount_factor.toFixed(3)], ["Present value", (y) => money(y.present_value)],
    ] : model === "rim" ? [
      ["ROE", (y) => pct(y.margin)], ["Net income", (y) => money(y.nopat)], ["Residual income", (y) => money(y.cash_flow)],
      ["Discount factor", (y) => y.discount_factor.toFixed(3)], ["Present value", (y) => money(y.present_value)],
    ] : [
      ["Growth", (y) => pct(y.growth)], ["Dividend", (y) => perShare(y.cash_flow)],
      ["Discount factor", (y) => y.discount_factor.toFixed(3)], ["Present value", (y) => perShare(y.present_value)],
    ];
  return (
    <Panel kicker="BASE CASE" title="Ten-year projection">
      <div className="table-wrap">
        <table className="data-table">
          <thead><tr><th>Year</th>{columns.map(([label]) => <th className="num" key={label}>{label}</th>)}</tr></thead>
          <tbody>
            {years.map((year) => (
              <tr className={year.year === 6 ? "stage-two" : undefined} key={year.year}>
                <td>{year.year}</td>
                {columns.map(([label, format]) => <td className="num" key={label}>{format(year)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SensitivityPanel({ grid, price }: { grid: ValuationGrid; price: number | null }) {
  const range = gridRange(grid.values);
  const centre = Math.floor(grid.rows.length / 2);
  const rowLabel = DRIVER_LABELS[grid.row_label] ?? grid.row_label;
  const columnLabel = DRIVER_LABELS[grid.column_label] ?? grid.column_label;
  return (
    <Panel kicker="SENSITIVITY · VALUE PER SHARE" title={`${rowLabel} × ${columnLabel}`}>
      <div className="table-wrap">
        <table className="heatmap">
          <thead>
            <tr>
              <th scope="col">{rowLabel} ↓ / {columnLabel} →</th>
              {grid.columns.map((column) => <th className="num" key={column} scope="col">{pct(column)}</th>)}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row, i) => (
              <tr key={row}>
                <th className="num" scope="row">{pct(row)}</th>
                {grid.columns.map((column, j) => {
                  const value = grid.values[i][j];
                  const upside = value !== null && price ? value / price - 1 : null;
                  return (
                    <td className={`heat-${heatTone(value, price, range)}${i === centre && j === centre ? " is-base" : ""}`} key={column}
                      title={`${rowLabel} ${pct(row)}, ${columnLabel} ${pct(column)}: ${value === null ? "no finite value (discount rate must exceed terminal growth)" : perShare(value)}${upside !== null ? `, ${upsideText(upside)}` : ""}`}>
                      {value === null ? "—" : perShare(value)}
                      {upside !== null && <small>{formatPercent(upside * 100, 0)}</small>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <HeatLegend price={price} range={range} />
    </Panel>
  );
}

function HeatLegend({ price, range }: { price: number | null; range: [number, number] }) {
  const items: [string, string][] = price
    ? [["below-2", "≤ −30% vs price"], ["below-1", "−30% to −10%"], ["near", "within ±10%"], ["above-1", "+10% to +30%"], ["above-2", "≥ +30%"]]
    : [["low", `lower third (from ${perShare(range[0])})`], ["mid", "middle third"], ["high", `upper third (to ${perShare(range[1])})`]];
  return (
    <ul className="heat-legend" aria-label="Colour key">
      {items.map(([tone, label]) => <li key={tone}><i className={`heat-${tone}`} />{label}</li>)}
      <li><i className="heat-base-key" />base case</li>
    </ul>
  );
}

function SavedRuns({ ticker, request, onLoad }: {
  ticker: string;
  request: ValuationRequest | null;
  onLoad: (run: { name: string; model: ValuationModel; request: Record<string, unknown> }) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const runs = useQuery({ queryKey: ["valuation-runs", ticker], queryFn: () => api.valuationRuns(ticker) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["valuation-runs", ticker] });
  const save = useMutation({
    mutationFn: () => api.saveValuationRun(ticker, { ...request!, name: name.trim() }),
    onSuccess: () => { setName(""); refresh(); },
  });
  const remove = useMutation({ mutationFn: api.deleteValuationRun, onSuccess: refresh });
  const load = useMutation({
    mutationFn: api.valuationRun,
    onSuccess: (run) => onLoad({ name: run.name, model: run.model, request: run.request }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (request && name.trim()) save.mutate();
  }

  return (
    <Panel kicker="SAVED RUNS · PRIVATE TO YOU" title="Valuation history">
      <form className="inline-form" onSubmit={submit}>
        <input aria-label="Run name" maxLength={120} placeholder="Name this valuation, e.g. Base DCF Oct 2026"
          value={name} onChange={(event) => setName(event.target.value)} />
        <button className="button-primary" disabled={!request || !name.trim() || save.isPending} type="submit">
          <Save size={13} /> Save
        </button>
      </form>
      {save.isError && <ErrorNotice error={save.error} title="Not saved" />}
      {runs.isError && <ErrorNotice error={runs.error} title="Saved runs unavailable" />}
      {runs.data && runs.data.length === 0 && <p className="panel-copy">No saved valuations for this company yet.</p>}
      {runs.data && runs.data.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Run</th><th>Model</th><th className="num">Base value</th><th className="num">Price then</th><th /></tr>
            </thead>
            <tbody>
              {runs.data.map((run) => (
                <tr key={run.id}>
                  <td>{run.name}<small>{formatDateTime(run.created_at)} · formulas {run.formula_version}</small></td>
                  <td>{run.model.toUpperCase()}</td>
                  <td className="num">{perShare(run.base_per_share)}</td>
                  <td className="num">{perShare(run.price)}</td>
                  <td className="rule-actions">
                    <button className="button-secondary" type="button" onClick={() => load.mutate(run.id)}>Load</button>
                    <button aria-label={`Delete ${run.name}`} className="button-secondary" type="button"
                      onClick={() => remove.mutate(run.id)}><Trash2 size={13} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel-copy">A saved run keeps the exact inputs, where each came from, the results, and the formula version.</p>
    </Panel>
  );
}

function RelativePanel({ ticker }: { ticker: string }) {
  const query = useQuery({ queryKey: ["valuation-relative", ticker], queryFn: () => api.relativeValuation(ticker) });
  const data: RelativeValuation | undefined = query.data;
  const multiple = (value: number | null | undefined) => formatValue(value, "multiple");
  return (
    <Panel kicker={data ? `RELATIVE · ${data.peer_basis.toUpperCase()}` : "RELATIVE"} title="Multiples vs peers and history">
      {query.isPending && <div className="chart-placeholder">Comparing multiples…</div>}
      {query.isError && <ErrorNotice error={query.error} title="Relative valuation unavailable" />}
      {data && (
        <>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Multiple</th><th className="num">Company</th><th className="num">Peer median</th>
                  <th className="num">Industry median</th><th className="num">5-yr median</th><th className="num">Implied value</th>
                </tr>
              </thead>
              <tbody>
                {data.multiples.map((m) => (
                  <tr key={m.key}>
                    <td>{m.label}<small>on {m.denominator}</small></td>
                    <td className="num">{multiple(m.company)}</td>
                    <td className="num">{multiple(m.peer_median)}<small>{m.peer_count} peers</small></td>
                    <td className="num">{multiple(m.industry_median)}<small>{m.industry_count} companies</small></td>
                    <td className="num">{multiple(m.history_median)}<small>{m.history_years} years</small></td>
                    <td className="num">
                      {perShare(m.implied_per_share)}
                      {m.implied_per_share !== null && data.price ? <small>{upsideText(m.implied_per_share / data.price - 1)}</small> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="panel-copy">
            {data.peers.length ? `Peers: ${data.peers.join(", ")}. ` : "No peers with stored data yet. "}
            {data.industry_basis ? `Industry: ${data.industry_basis}. ` : ""}{data.note}
          </p>
          {!data.price && <StatusPill tone="warn">NO STORED PRICE · MULTIPLES NEED PRICE HISTORY</StatusPill>}
        </>
      )}
    </Panel>
  );
}
