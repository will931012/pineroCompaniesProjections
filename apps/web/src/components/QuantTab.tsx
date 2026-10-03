"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, type Factors, type Predictions } from "@/lib/api/endpoints";
import { formatDate } from "@/lib/format";
import { featureLabel, formatFeature, percentileLabel, probability, signedPct } from "@/lib/quant";
import { ErrorNotice, Panel, StatusPill } from "./ui";

export function QuantTab({ ticker }: { ticker: string }) {
  const factors = useQuery({ queryKey: ["factors", ticker], queryFn: () => api.factors(ticker) });
  const predictions = useQuery({ queryKey: ["predictions", ticker], queryFn: () => api.predictions(ticker) });
  return (
    <div className="company-tab-body">
      {predictions.isError && <ErrorNotice error={predictions.error} title="Predictions unavailable" />}
      {predictions.data && <PredictionsPanel data={predictions.data} />}
      {factors.isError && <ErrorNotice error={factors.error} title="Factor scores unavailable" />}
      {factors.data && <FactorsPanel data={factors.data} />}
    </div>
  );
}

function PredictionsPanel({ data }: { data: Predictions }) {
  if (data.predictions.length === 0) {
    return (
      <Panel kicker="MODEL OUTPUT" title="No prediction yet">
        <p className="panel-copy">
          {data.in_universe
            ? "Predictions appear once models are trained and this company's latest month-end features are built."
            : "Models cover the model universe: the largest companies by SEC-reported revenue. This company is not in it."}
          {" "}<Link href="/models">See the models</Link>.
        </p>
      </Panel>
    );
  }
  const validated = data.predictions.some((p) => p.validated);
  const cards = (
      <div className="prediction-cards">
        {data.predictions.map((p) => (
          <section className="prediction-card" key={p.model_id}>
            <span className="section-kicker">NEXT {p.horizon_days} TRADING DAYS · FROM {formatDate(p.as_of)}</span>
            <strong>{probability(p.probability)}</strong>
            <small>calibrated probability ({p.calibration}); raw model {probability(p.probability_raw)}</small>
            <p className="prediction-range">
              Excess return forecast {signedPct(p.expected_excess)}, 10th–90th percentile {signedPct(p.excess_low)} to {signedPct(p.excess_high)}
            </p>
            <h3 className="subheading">Largest contributions</h3>
            <ul className="driver-list">
              {p.drivers.map((d) => (
                <li key={d.feature}>
                  <span className="driver-name">{featureLabel(d.feature)}</span>
                  <span className="driver-value">
                    {formatFeature(d.feature, d.value)}
                    {d.percentile !== null ? ` · ${percentileLabel(d.percentile)}` : ""}
                  </span>
                  <span className={`driver-bar ${d.contribution >= 0 ? "up" : "down"}`}>
                    <i style={{ width: `${Math.min(100, Math.abs(d.contribution) * 400)}%` }} />
                    {d.contribution >= 0 ? "raises" : "lowers"} {Math.abs(d.contribution).toFixed(3)}
                  </span>
                </li>
              ))}
            </ul>
            <small>
              Model {p.model_name}, trained through {formatDate(p.trained_through)}; inputs published by {formatDate(p.data_available_on)}.
              Out of sample: AUC {p.model_oos_auc?.toFixed(3) ?? "—"}, rank IC {p.model_oos_rank_ic?.toFixed(3) ?? "—"}.
            </small>
          </section>
        ))}
      </div>
  );
  return (
    <Panel kicker="MODEL OUTPUT · NOT ADVICE" title={validated ? "Chance of beating the S&P 500" : "Models not validated"}
      action={<StatusPill tone={validated ? "neutral" : "warn"}>{validated ? "MODEL OUTPUT" : "NOT VALIDATED"}</StatusPill>}>
      {validated ? cards : (
        <>
          <p className="panel-copy">
            Out of sample, no model passed the validation rule, so their probabilities are not offered as guidance.
            They are still recorded and scored each month on the <Link href="/models">Models page</Link>.
          </p>
          <ul className="validation-list">
            {data.predictions.map((p) => (
              <li key={p.model_id}>
                <b>{p.horizon_days}-day model</b>: AUC {p.model_oos_auc?.toFixed(3) ?? "—"} (0.5 = no skill), rank IC {p.model_oos_rank_ic?.toFixed(3) ?? "—"}
              </li>
            ))}
          </ul>
          <details className="unvalidated-output">
            <summary>Show the unvalidated output anyway</summary>
            {cards}
          </details>
        </>
      )}
      <p className="panel-copy">{data.disclaimer} Contributions are SHAP values in log-odds of the raw model.</p>
      {data.history.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Month end</th><th>Model</th><th className="num">Probability</th><th className="num">Forecast</th><th className="num">Realised excess</th></tr></thead>
            <tbody>
              {data.history.map((h) => (
                <tr key={`${h.as_of}-${h.model_name}`}>
                  <td>{formatDate(h.as_of)}</td>
                  <td>{h.model_name}</td>
                  <td className="num">{probability(h.probability)}</td>
                  <td className="num">{signedPct(h.expected_excess)}</td>
                  <td className="num">{h.realised_excess === null ? "pending" : `${signedPct(h.realised_excess)} ${h.went_up ? "▲" : "▼"}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function FactorsPanel({ data }: { data: Factors }) {
  const kicker = data.in_universe
    ? `FACTORS · RANK ${data.universe_rank} OF ${data.universe_size} BY REVENUE`
    : "FACTORS";
  if (data.factors.length === 0) {
    return (
      <Panel kicker={kicker} title="No factor scores yet">
        <p className="panel-copy">
          {data.in_universe
            ? "Scores appear after this company's month-end features are built (they need a year of stored prices)."
            : "Factor scores are computed for the model universe only."}
        </p>
      </Panel>
    );
  }
  return (
    <Panel kicker={`${kicker} · ${formatDate(data.as_of)}`} title="Factor profile">
      <ul className="factor-list">
        {data.factors.map((f) => (
          <li key={f.key}>
            <div className="factor-head">
              <b>{f.label}</b>
              <span>{percentileLabel(f.percentile)} · score {f.score.toFixed(2)}</span>
            </div>
            <div className="factor-bar" role="img" aria-label={`${f.label}: ${percentileLabel(f.percentile)}`}>
              <i style={{ width: `${f.percentile * 100}%` }} />
            </div>
            <details>
              <summary>Inputs</summary>
              <table className="data-table">
                <tbody>
                  {f.inputs.map((input) => (
                    <tr key={input.feature}>
                      <td>{featureLabel(input.feature)}</td>
                      <td className="num">{formatFeature(input.feature, input.value)}</td>
                      <td className="num">z {input.z.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </li>
        ))}
      </ul>
      <p className="panel-copy">{data.note}</p>
    </Panel>
  );
}
