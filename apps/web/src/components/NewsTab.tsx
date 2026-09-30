"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api/endpoints";
import { formatDateTime, formatInteger } from "@/lib/format";
import { hasRole, useSession } from "@/lib/session";
import { EventTimeline } from "./EventTimeline";
import { ErrorNotice, Panel, SourceList, StatusPill } from "./ui";

const WINDOWS = [7, 30, 90, 365] as const;

export function NewsTab({ ticker }: { ticker: string }) {
  const { data: session } = useSession();
  const queryClient = useQueryClient();
  const [view, setView] = useState<"events" | "headlines">("events");
  const [days, setDays] = useState<number>(30);
  const [showAll, setShowAll] = useState(false);
  const events = useQuery({
    queryKey: ["events", ticker, days, showAll],
    queryFn: () => api.events(ticker, days, showAll),
    enabled: view === "events",
    placeholderData: (previous) => previous,
  });
  const news = useQuery({
    queryKey: ["news", ticker, days, showAll],
    queryFn: () => api.news(ticker, days, showAll ? "all" : "high"),
    enabled: view === "headlines",
    placeholderData: (previous) => previous,
  });
  const refresh = useMutation({
    mutationFn: () => api.refreshNews(ticker),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["events", ticker] });
      queryClient.invalidateQueries({ queryKey: ["news", ticker] });
    },
  });

  const controls = (
    <div className="chart-controls">
      <div className="segmented" role="group" aria-label="View">
        {(["events", "headlines"] as const).map((v) => (
          <button aria-pressed={view === v} key={v} onClick={() => setView(v)} type="button">
            {v === "events" ? "Events" : "Headlines"}
          </button>
        ))}
      </div>
      <div className="segmented" role="group" aria-label="Window">
        {WINDOWS.map((value) => (
          <button aria-pressed={value === days} key={value} onClick={() => setDays(value)} type="button">
            {value === 365 ? "1Y" : `${value}D`}
          </button>
        ))}
      </div>
      <label className="as-of-control">
        <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
        <span>{view === "events" ? "Include repeats" : "Include weak matches"}</span>
      </label>
    </div>
  );

  return (
    <div className="company-tab-body">
      <Panel kicker="SEC 8-K EVENTS · NEWS VIA GDELT" title="News & events" action={controls}>
        {hasRole(session?.user.role, "analyst") && (
          <div className="index-controls">
            <button className="button-secondary" disabled={refresh.isPending} onClick={() => refresh.mutate()} type="button"
              title="One request to the GDELT news index (it allows one every 5 seconds)">
              <RefreshCw size={14} className={refresh.isPending ? "spin" : ""} />
              {refresh.isPending ? "Searching news…" : "Refresh news"}
            </button>
            {refresh.data && (
              <small>
                {refresh.data.status === "current"
                  ? `${formatInteger(refresh.data.articles)} articles found, ${formatInteger(refresh.data.new)} new, ${formatInteger(refresh.data.events)} events`
                  : refresh.data.message}
              </small>
            )}
            {refresh.isError && <ErrorNotice error={refresh.error} title="Refresh failed" />}
          </div>
        )}

        {view === "events" && (
          <>
            {events.isPending && <div className="chart-placeholder">Loading events…</div>}
            {events.isError && <ErrorNotice error={events.error} title="Events unavailable" />}
            {events.data && (
              <>
                <div className="event-counts">
                  {Object.entries(events.data.counts).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
                    <StatusPill key={type} tone="neutral">{type.replaceAll("_", " ").toUpperCase()} · {count}</StatusPill>
                  ))}
                </div>
                {events.data.events.length === 0
                  ? <p className="panel-copy">No events in this window. 8-K events appear once the SEC tab has loaded the filing index.</p>
                  : <EventTimeline events={events.data.events} />}
                <p className="panel-copy">
                  8-K events are classified by their items; news by headline keywords (hover an event for the exact rule).
                  A story reported by several outlets appears once, as its first report. Classifier {events.data.classifier_version}.
                </p>
              </>
            )}
          </>
        )}

        {view === "headlines" && (
          <>
            {news.isPending && <div className="chart-placeholder">Loading headlines…</div>}
            {news.isError && <ErrorNotice error={news.error} title="News unavailable" />}
            {news.data && (
              <>
                {news.data.items.length === 0 ? (
                  <p className="panel-copy">No headlines stored for this window yet.</p>
                ) : (
                  <ol className="event-timeline">
                    {news.data.items.map((item) => (
                      <li className="event-row event-news" key={item.id}>
                        <span className="event-time">{formatDateTime(item.seen_at)}</span>
                        <div className="event-body">
                          <div className="event-meta">
                            <span className="event-source">{item.domain}</span>
                            {item.event && <span className="event-type">{item.event.label}</span>}
                            {item.event?.novelty === "repeat" && <span className="event-repeat">repeat coverage</span>}
                            {item.confidence === "low" && <span className="event-repeat" title="The headline does not name the company; GDELT matched the article text">weak match</span>}
                          </div>
                          <a className="event-title" href={item.url} rel="noreferrer noopener" target="_blank">
                            {item.title} <ExternalLink size={11} />
                          </a>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
                <p className="panel-copy">
                  {news.data.low_confidence_hidden > 0 && !showAll && `${formatInteger(news.data.low_confidence_hidden)} weak matches hidden (the headline does not name the company). `}
                  {news.data.last_refreshed ? `Last searched ${formatDateTime(news.data.last_refreshed)}. ` : "Not searched yet. "}
                  {news.data.attribution} Pinero shows headlines and links only.
                </p>
                <details className="sources">
                  <summary>Sources ({news.data.sources.length})</summary>
                  <SourceList sources={news.data.sources} />
                </details>
              </>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
