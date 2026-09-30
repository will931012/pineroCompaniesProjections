"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api/endpoints";
import { viewerHref } from "@/lib/filings";
import { formatDate, formatInteger } from "@/lib/format";
import { ErrorNotice, Panel } from "./ui";

export function EarningsTab({ ticker }: { ticker: string }) {
  const query = useQuery({ queryKey: ["earnings", ticker], queryFn: () => api.earnings(ticker) });
  const data = query.data;
  return (
    <div className="company-tab-body">
      <Panel kicker="SEC 8-K ITEM 2.02" title="Earnings releases">
        {query.isPending && <div className="chart-placeholder">Loading earnings releases…</div>}
        {query.isError && <ErrorNotice error={query.error} title="Earnings unavailable" />}
        {data && data.releases.length === 0 && (
          <p className="panel-copy">No earnings 8-Ks indexed yet. Open the SEC tab to load the filing index.</p>
        )}
        {data && data.releases.length > 0 && (
          <ol className="earnings-list">
            {data.releases.map((release) => (
              <li key={release.filing.accession}>
                <div className="earnings-head">
                  <b>{formatDate(release.occurred_at)}</b>
                  <span>8-K · {release.filing.accession}</span>
                  <Link href={viewerHref(ticker, release.filing.accession, { section: "exhibit_99" })}>
                    {release.press_release_chars ? "Read press release" : "Open filing"}
                  </Link>
                  <a href={release.filing.sec_url} rel="noreferrer noopener" target="_blank">SEC <ExternalLink size={11} /></a>
                </div>
                {release.press_release_excerpt ? (
                  <p className="earnings-excerpt">
                    {release.press_release_excerpt}
                    {release.press_release_chars && release.press_release_chars > release.press_release_excerpt.length ? "…" : ""}
                    <small> {formatInteger(release.press_release_chars)} characters in the full release</small>
                  </p>
                ) : (
                  <p className="panel-copy">
                    {release.document_status === "loaded"
                      ? "No press release exhibit was attached to this 8-K."
                      : "The press release loads when the filing is opened."}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
        {data && <p className="panel-copy">{data.note}</p>}
      </Panel>
    </div>
  );
}
