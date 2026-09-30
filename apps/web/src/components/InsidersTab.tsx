"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { api, type InsiderTransaction, type InsidersResponse } from "@/lib/api/endpoints";
import { transactionLabel } from "@/lib/filings";
import { formatDate, formatInteger, formatPrice, formatValue } from "@/lib/format";
import { ErrorNotice, Panel, StatusPill } from "./ui";

const WINDOWS = [6, 12, 24] as const;

function role(t: InsiderTransaction): string {
  const roles = [
    t.officer_title ?? (t.is_officer ? "Officer" : null),
    t.is_director ? "Director" : null,
    t.is_ten_percent_owner ? "10% owner" : null,
  ].filter(Boolean);
  return roles.join(" · ") || "Other";
}

function Totals({ label, totals, tone }: {
  label: string;
  totals: InsidersResponse["summary"]["purchases"];
  tone: "buy" | "sell";
}) {
  return (
    <div className={`insider-total insider-${tone}`}>
      <span>{label}</span>
      <strong>{formatValue(totals.value, "currency")}</strong>
      <small>
        {formatInteger(totals.transactions)} transactions · {formatValue(totals.shares, "shares")} shares ·{" "}
        {formatInteger(totals.insiders)} insiders
      </small>
    </div>
  );
}

export function InsidersTab({ ticker }: { ticker: string }) {
  const [months, setMonths] = useState<number>(12);
  const [load, setLoad] = useState(25);
  const [openMarketOnly, setOpenMarketOnly] = useState(false);
  const query = useQuery({
    queryKey: ["insiders", ticker, months, load],
    queryFn: () => api.insiders(ticker, months, load),
    placeholderData: (previous) => previous,
  });
  const data = query.data;
  const rows = (data?.transactions ?? []).filter(
    (t) => !openMarketOnly || (!t.is_derivative && (t.transaction_code === "P" || t.transaction_code === "S")),
  );

  const controls = (
    <div className="chart-controls">
      <div className="segmented" role="group" aria-label="Window">
        {WINDOWS.map((value) => (
          <button aria-pressed={value === months} key={value} onClick={() => setMonths(value)} type="button">
            {value}M
          </button>
        ))}
      </div>
      <label className="as-of-control">
        <input type="checkbox" checked={openMarketOnly} onChange={(event) => setOpenMarketOnly(event.target.checked)} />
        <span>Open market only</span>
      </label>
    </div>
  );

  return (
    <div className={`company-tab-body${query.isFetching && data ? " is-refetching" : ""}`}>
      <Panel kicker={`SEC FORM 4 · LAST ${months} MONTHS`} title="Insider transactions" action={controls}>
        {query.isPending && <div className="chart-placeholder">Fetching Form 4 filings from SEC…</div>}
        {query.isError && <ErrorNotice error={query.error} title="Insider data unavailable" />}
        {data?.message && <p className="quality-warning">{data.message}</p>}
        {data && (
          <>
            <div className="insider-totals">
              <Totals label="Open-market purchases (P)" totals={data.summary.purchases} tone="buy" />
              <Totals label="Open-market sales (S)" totals={data.summary.sales} tone="sell" />
            </div>
            <p className="panel-copy">
              Totals count non-derivative open-market trades since {formatDate(data.summary.since)}; grants, option
              exercises, tax withholding and gifts are listed but not counted. Values are shares × reported price;
              a trade without a reported price adds no value. {formatInteger(data.filings_loaded)} Form 4 filings
              parsed
              {data.filings_pending > 0 && (
                <>
                  , {formatInteger(data.filings_pending)} not fetched yet{" "}
                  <button className="button-link" disabled={query.isFetching} type="button"
                    onClick={() => setLoad(60)}>Load up to 60 more</button>
                </>
              )}
              .
            </p>
          </>
        )}
        {data && rows.length === 0 && <p className="panel-copy">No transactions in this window.</p>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table className="data-table insider-table">
              <thead>
                <tr>
                  <th>Date</th><th>Insider</th><th>Transaction</th><th className="num">Shares</th>
                  <th className="num">Price</th><th className="num">Value</th><th className="num">Owned after</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={`${t.accession}-${t.is_derivative}-${t.security_title}-${t.transaction_date}-${t.shares}-${t.transaction_code}`}
                    className={t.transaction_code === "P" ? "row-buy" : t.transaction_code === "S" ? "row-sell" : undefined}>
                    <td className="mono">{t.transaction_date ?? "—"}</td>
                    <td>
                      {t.owner_name}
                      <small>{role(t)}{t.joint_owners > 0 ? ` · +${t.joint_owners} joint filers` : ""}</small>
                    </td>
                    <td>
                      {transactionLabel(t.transaction_code)}
                      <small>
                        {[t.security_title, t.is_derivative && t.underlying_security ? `on ${t.underlying_security}` : null,
                          t.ownership === "I" ? `indirect${t.ownership_nature ? ` (${t.ownership_nature})` : ""}` : null,
                          t.form.endsWith("/A") ? "amended filing" : null].filter(Boolean).join(" · ")}
                      </small>
                      {t.rule_10b5_1 && <StatusPill tone="neutral">10b5-1 PLAN</StatusPill>}
                    </td>
                    <td className="num">
                      {t.acquired_disposed === "D" ? "−" : t.acquired_disposed === "A" ? "+" : ""}
                      {t.shares !== null && t.shares !== undefined ? formatInteger(t.shares) : "—"}
                    </td>
                    <td className="num">{formatPrice(t.price)}</td>
                    <td className="num">{formatValue(t.value, "currency")}</td>
                    <td className="num">{t.shares_owned_after !== null && t.shares_owned_after !== undefined ? formatInteger(t.shares_owned_after) : "—"}</td>
                    <td>
                      <a href={t.sec_url}
                        rel="noreferrer noopener" target="_blank" title={`Form ${t.form} filed ${t.filed_date}`}>
                        <ExternalLink size={12} />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
