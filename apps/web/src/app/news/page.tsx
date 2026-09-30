"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { EventTimeline } from "@/components/EventTimeline";
import { ErrorNotice, PageHeading, Panel } from "@/components/ui";
import { api } from "@/lib/api/endpoints";
import { formatInteger } from "@/lib/format";

const WINDOWS = [1, 7, 30] as const;

export default function NewsPage() {
  const [days, setDays] = useState<number>(7);
  const feed = useQuery({
    queryKey: ["feed", days],
    queryFn: () => api.feed(days),
    placeholderData: (previous) => previous,
  });
  const data = feed.data;
  const tickers = new Map(data?.items.map((i) => [i.event.id, i.ticker]) ?? []);

  return (
    <>
      <PageHeading kicker="NEWS & EVENTS" title="News"
        subtitle="First reports of SEC 8-K events and news for the companies on your watchlists." />
      <Panel kicker={data ? `${formatInteger(data.companies)} WATCHLIST COMPANIES` : "WATCHLISTS"} title="Latest events"
        action={
          <div className="segmented" role="group" aria-label="Window">
            {WINDOWS.map((value) => (
              <button aria-pressed={value === days} key={value} onClick={() => setDays(value)} type="button">
                {value === 1 ? "24H" : `${value}D`}
              </button>
            ))}
          </div>
        }>
        {feed.isPending && <div className="chart-placeholder">Loading events…</div>}
        {feed.isError && <ErrorNotice error={feed.error} title="Feed unavailable" />}
        {data && data.companies === 0 && (
          <p className="panel-copy">
            Add companies to a watchlist on the <Link href="/">dashboard</Link> or a company page. The background
            worker then checks their filings every 30 minutes and the GDELT news index every hour.
          </p>
        )}
        {data && data.companies > 0 && data.items.length === 0 && (
          <p className="panel-copy">No events in this window yet.</p>
        )}
        {data && data.items.length > 0 && (
          <EventTimeline events={data.items.map((i) => i.event)} tickerOf={(e) => tickers.get(e.id) ?? ""} />
        )}
        <p className="panel-copy">
          News index: The GDELT Project. Headlines link to their publishers; Pinero does not copy articles.
        </p>
      </Panel>
    </>
  );
}
