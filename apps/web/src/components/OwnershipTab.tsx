"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { api } from "@/lib/api/endpoints";
import { formatDate, formatInteger, formatValue } from "@/lib/format";
import { OWNERSHIP_CHANGES } from "@/lib/quant";
import { ErrorNotice, Panel } from "./ui";

const secFiling = (cik: number, accession: string) =>
  `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replaceAll("-", "")}/`;

export function OwnershipTab({ ticker }: { ticker: string }) {
  const query = useQuery({ queryKey: ["ownership", ticker], queryFn: () => api.ownership(ticker) });
  const data = query.data;
  const [latest, previous] = data?.periods ?? [];
  return (
    <div className="company-tab-body">
      {query.isPending && <div className="chart-placeholder">Loading 13F holdings…</div>}
      {query.isError && <ErrorNotice error={query.error} title="Ownership unavailable" />}
      {data && !data.available && (
        <Panel kicker="FORM 13F" title="No institutional holdings loaded">
          <p className="panel-copy">Holdings come from SEC&apos;s quarterly Form 13F data sets, which the worker loads weekly. {data.note}</p>
        </Panel>
      )}
      {data?.available && latest && (
        <>
          <Panel kicker={`FORM 13F · QUARTER ENDING ${formatDate(latest.period).toUpperCase()}`} title="Institutional ownership">
            <dl className="stat-grid">
              <div><dt>Managers holding</dt><dd>{formatInteger(latest.holders)}{previous ? <small> ({latest.holders - previous.holders >= 0 ? "+" : "−"}{formatInteger(Math.abs(latest.holders - previous.holders))})</small> : null}</dd></div>
              <div><dt>Reported value</dt><dd>{formatValue(latest.value_usd, "currency")}</dd></div>
              <div><dt>Shares reported</dt><dd>{formatValue(latest.shares, "shares")}{previous && previous.shares > 0 ? <small> ({((latest.shares / previous.shares - 1) * 100).toFixed(1)}%)</small> : null}</dd></div>
              <div><dt>Share of market cap</dt><dd>{latest.share_of_market_cap === null ? "—" : formatValue(latest.share_of_market_cap, "percent")}</dd></div>
            </dl>
            {data.cusips.length > 1 && (
              <p className="panel-copy">
                This company has {data.cusips.length} share classes in 13F filings; share counts add the classes together and are
                not comparable to one class&apos;s price. Values are in dollars and do add up.
              </p>
            )}
            {data.periods.length > 1 && (
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr><th>Quarter</th><th className="num">Managers</th><th className="num">Shares</th><th className="num">Value</th><th className="num">Of market cap</th></tr></thead>
                  <tbody>
                    {data.periods.map((p) => (
                      <tr key={p.period}>
                        <td>{formatDate(p.period)}</td>
                        <td className="num">{formatInteger(p.holders)}</td>
                        <td className="num">{formatValue(p.shares, "shares")}</td>
                        <td className="num">{formatValue(p.value_usd, "currency")}</td>
                        <td className="num">{p.share_of_market_cap === null ? "—" : formatValue(p.share_of_market_cap, "percent")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <Panel kicker="LARGEST HOLDERS" title={`Top ${data.holders.length} managers`}>
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>Manager</th><th className="num">Shares</th><th className="num">Value</th><th className="num">Change</th><th>Filing</th></tr></thead>
                <tbody>
                  {data.holders.map((h) => (
                    <tr key={h.filer_cik}>
                      <td>{h.filer_name}<small>CIK {h.filer_cik}</small></td>
                      <td className="num">{formatInteger(h.shares)}</td>
                      <td className="num">{formatValue(h.value_usd, "currency")}</td>
                      <td className="num">
                        {OWNERSHIP_CHANGES[h.change] ?? h.change}
                        {h.previous_shares !== null && h.previous_shares > 0 && h.change !== "unchanged"
                          ? <small>{((h.shares / h.previous_shares - 1) * 100).toFixed(1)}%</small> : null}
                      </td>
                      <td>
                        <a href={secFiling(h.filer_cik, h.accession)} rel="noreferrer noopener" target="_blank">
                          {formatDate(h.filing_date)} <ExternalLink size={11} />
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data.sold_out.length > 0 && (
              <>
                <h3 className="subheading">Left the top 100 since the previous quarter</h3>
                <p className="panel-copy">{data.sold_out.map((h) => h.filer_name).join(", ")}</p>
              </>
            )}
            <p className="panel-copy">{data.note} CUSIP{data.cusips.length > 1 ? "s" : ""}: {data.cusips.join(", ")} (mapped with OpenFIGI).</p>
          </Panel>
        </>
      )}
    </div>
  );
}
