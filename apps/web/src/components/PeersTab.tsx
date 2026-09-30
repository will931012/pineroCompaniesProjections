"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { api } from "@/lib/api/endpoints";
import { formatValue } from "@/lib/format";
import { ErrorNotice, Panel } from "./ui";

export function useScreenMetrics() {
  return useQuery({ queryKey: ["screen-metrics"], queryFn: api.screenMetrics, staleTime: Infinity });
}

export function PeersTab({ ticker }: { ticker: string }) {
  const [draft, setDraft] = useState("");
  const [extra, setExtra] = useState("");
  const peers = useQuery({ queryKey: ["peers", ticker, extra], queryFn: () => api.peers(ticker, extra) });
  const catalog = useScreenMetrics();
  const definitions = new Map((catalog.data ?? []).map((m) => [m.key, m]));

  function submit(event: FormEvent) {
    event.preventDefault();
    setExtra(draft.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean).join(","));
  }

  const data = peers.data;
  return (
    <Panel kicker={data ? `PEERS · ${data.basis.toUpperCase()}` : "PEERS"} title="Peer comparison">
      <form className="inline-form" onSubmit={submit}>
        <input aria-label="Additional peer tickers" placeholder="Add peers, e.g. MSFT, GOOGL" value={draft}
          onChange={(event) => setDraft(event.target.value)} />
        <button className="button-secondary" type="submit">Compare</button>
      </form>
      {peers.isPending && <div className="chart-placeholder">Finding peers…</div>}
      {peers.isError && <ErrorNotice error={peers.error} title="Peers unavailable" />}
      {data && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Company</th>
                {data.columns.map((key) => (
                  <th className="num" key={key} title={definitions.get(key)?.description}>{definitions.get(key)?.label ?? key}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr className={row.is_subject ? "row-subject" : undefined} key={row.ticker}>
                  <td>
                    <Link href={`/companies/${encodeURIComponent(row.ticker)}?tab=financials`}>{row.ticker}</Link>
                    <small>{row.name}{row.sic_code ? ` · SIC ${row.sic_code}` : ""}</small>
                  </td>
                  {data.columns.map((key) => (
                    <td className="num" key={key}>{formatValue(row.metrics[key], definitions.get(key)?.unit ?? "ratio")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel-copy">
        Peers share the SEC SIC code (or its two-digit major group when fewer than three do), ranked by revenue.
        Values are each company&apos;s latest stored snapshot, so a company shows blanks until its SEC financial data
        has been loaded. Price-based columns also need stored prices.
      </p>
    </Panel>
  );
}
